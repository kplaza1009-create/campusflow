"""Courses: create, edit, archive, and delete with an impact check."""

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.deps import DB, CurrentAuth, Now
from app.idempotency import IdempotencyKey
from app.models import Assignment, Course
from app.schemas import CourseCreate, CourseOut, CourseUpdate, DeletionImpact

router = APIRouter(prefix="/api/courses", tags=["courses"])


def get_owned_course(db: Session, user_id: int, course_id: int) -> Course:
    """Load a course only if it belongs to this user. Someone else's course gets
    the same 404 as a missing one, so IDs can't be probed."""
    course = db.scalar(select(Course).where(Course.id == course_id, Course.user_id == user_id))
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found.")
    return course


def _out(course: Course, open_count: int = 0) -> CourseOut:
    return CourseOut(
        id=course.id,
        name=course.name,
        code=course.code,
        instructor=course.instructor,
        color=course.color,
        archived=course.archived_at is not None,
        open_assignments=open_count,
    )


def _open_counts(db: Session, user_id: int) -> dict[int, int]:
    rows = db.execute(
        select(Assignment.course_id, func.count())
        .where(Assignment.user_id == user_id, Assignment.completed_at.is_(None))
        .group_by(Assignment.course_id)
    )
    return {course_id: count for course_id, count in rows}


@router.get("", response_model=list[CourseOut])
def list_courses(auth: CurrentAuth, db: DB, include_archived: bool = False) -> list[CourseOut]:
    stmt = select(Course).where(Course.user_id == auth.user.id)
    if not include_archived:
        stmt = stmt.where(Course.archived_at.is_(None))
    counts = _open_counts(db, auth.user.id)
    courses = db.scalars(stmt.order_by(Course.archived_at.is_not(None), func.lower(Course.name)))
    return [_out(c, counts.get(c.id, 0)) for c in courses]


@router.post("", response_model=CourseOut, status_code=status.HTTP_201_CREATED)
def create_course(
    body: CourseCreate, auth: CurrentAuth, db: DB, response: Response, key: IdempotencyKey = None
) -> CourseOut:
    stmt = (
        insert(Course)
        .values(user_id=auth.user.id, idempotency_key=key, **body.model_dump())
        .on_conflict_do_nothing(constraint="uq_courses_idempotency")
        .returning(Course.id)
    )
    new_id = db.scalar(stmt)
    db.commit()
    if new_id is None:  # same key seen before: return the original, don't insert again
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
        existing = db.scalar(
            select(Course).where(Course.user_id == auth.user.id, Course.idempotency_key == key)
        )
        assert existing is not None
        return _out(existing)
    return _out(get_owned_course(db, auth.user.id, new_id))


@router.patch("/{course_id}", response_model=CourseOut)
def update_course(course_id: int, body: CourseUpdate, auth: CurrentAuth, db: DB, now: Now) -> CourseOut:
    course = get_owned_course(db, auth.user.id, course_id)
    course.name, course.code, course.instructor, course.color = (
        body.name,
        body.code,
        body.instructor,
        body.color,
    )
    # Archiving keeps every assignment; it just hides the course from everyday views.
    if body.archived and course.archived_at is None:
        course.archived_at = now
    elif not body.archived:
        course.archived_at = None
    db.commit()
    return _out(course, _open_counts(db, auth.user.id).get(course.id, 0))


def _impact(db: Session, user_id: int, course_id: int) -> DeletionImpact:
    n = db.scalar(
        select(func.count()).where(Assignment.user_id == user_id, Assignment.course_id == course_id)
    )
    return DeletionImpact(assignments=n or 0)


@router.get("/{course_id}/deletion-impact", response_model=DeletionImpact)
def deletion_impact(course_id: int, auth: CurrentAuth, db: DB) -> DeletionImpact:
    get_owned_course(db, auth.user.id, course_id)
    return _impact(db, auth.user.id, course_id)


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(
    course_id: int,
    auth: CurrentAuth,
    db: DB,
    expected_assignments: int = Query(
        description="The assignment count the user confirmed. Must match the current count."
    ),
) -> None:
    # Lock the course row so the count can't change between checking and deleting.
    course = db.scalar(
        select(Course).where(Course.id == course_id, Course.user_id == auth.user.id).with_for_update()
    )
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found.")
    impact = _impact(db, auth.user.id, course_id)
    if impact.assignments != expected_assignments:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This course now has {impact.assignments} assignments. Review and confirm again.",
        )
    # Assignments go with it through ON DELETE CASCADE, in this same transaction.
    db.delete(course)
    db.commit()
