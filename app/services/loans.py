"""Library loan operations: borrowing and returning books."""
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Book, Loan, Member, MemberTier
from app.schemas import LoanCreate, LoanOut, LoanStatus
from app.services.members import ensure_can_access_restricted

# Maximum concurrent unreturned loans per tier (None = unlimited).
TIER_LOAN_LIMIT: Dict[str, Optional[int]] = {
    MemberTier.APPRENTICE.value: 1,
    MemberTier.ADEPT.value: 3,
    MemberTier.MASTER.value: 5,
    MemberTier.SUPREME.value: None,
}

LOAN_PERIOD = timedelta(days=14)
LATE_FEE_PER_DAY_CENTS = 25


def loan_status(loan: Loan, now: datetime) -> LoanStatus:
    """``returned`` if returned; else ``overdue`` if now > due_at; else ``active``."""
    if loan.returned_at is not None:
        return "returned"
    if now > loan.due_at:
        return "overdue"
    return "active"


def to_loan_out(loan: Loan, now: datetime) -> LoanOut:
    """Serialize a loan, computing its status at read time."""
    return LoanOut(
        id=loan.id,
        member_id=loan.member_id,
        book_id=loan.book_id,
        borrowed_at=loan.borrowed_at,
        due_at=loan.due_at,
        returned_at=loan.returned_at,
        late_fee_cents=loan.late_fee_cents,
        status=loan_status(loan, now),
    )


def calculate_late_fee(due_at: datetime, returned_at: datetime, price_cents: int) -> int:
    """25 cents per started day late (any partial day counts), capped at the book's price; 0 if not late."""
    if returned_at <= due_at:
        return 0
    diff = returned_at - due_at
    days_late = math.ceil(diff.total_seconds() / 86400)
    return min(days_late * LATE_FEE_PER_DAY_CENTS, price_cents)


def create_loan(db: Session, data: LoanCreate, now: datetime) -> LoanOut:
    """Borrow a book for 14 days."""
    # 1. Check member exists (404) & book exists (404)
    member = db.get(Member, data.member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    book = db.get(Book, data.book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")

    # 2. Check restricted books (403)
    if book.restricted:
        ensure_can_access_restricted(member)

    # 3. Check overdue loans (409)
    for l in member.loans:
        if l.returned_at is None and now > l.due_at:
            raise HTTPException(status_code=409, detail="Member has overdue loans")

    # 4. Check already borrowed this same book (409)
    for l in member.loans:
        if l.returned_at is None and l.book_id == book.id:
            raise HTTPException(status_code=409, detail="Book already borrowed by this member")

    # 5. Check tier loan limit (409)
    limit = TIER_LOAN_LIMIT.get(member.tier)
    if limit is not None:
        active_count = sum(1 for l in member.loans if l.returned_at is None)
        if active_count >= limit:
            raise HTTPException(status_code=409, detail="Member reached tier loan limit")

    # 6. Check stock (409)
    if book.stock < 1:
        raise HTTPException(status_code=409, detail="Book is out of stock")

    # Decrement stock and create loan
    book.stock -= 1
    loan = Loan(
        member_id=member.id,
        book_id=book.id,
        borrowed_at=now,
        due_at=now + LOAN_PERIOD,
        returned_at=None,
        late_fee_cents=0,
    )
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return to_loan_out(loan, now)


def get_loan(db: Session, loan_id: int, now: datetime) -> LoanOut:
    """Return a loan by id, or raise 404."""
    loan = db.get(Loan, loan_id)
    if loan is None:
        raise HTTPException(status_code=404, detail="Loan not found")
    return to_loan_out(loan, now)


def return_loan(db: Session, loan_id: int, now: datetime) -> LoanOut:
    """Return a borrowed book."""
    loan = db.get(Loan, loan_id)
    if loan is None:
        raise HTTPException(status_code=404, detail="Loan not found")
    if loan.returned_at is not None:
        raise HTTPException(status_code=409, detail="Loan already returned")

    loan.returned_at = now
    loan.late_fee_cents = calculate_late_fee(loan.due_at, now, loan.book.price_cents)
    loan.book.stock += 1

    db.commit()
    db.refresh(loan)
    return to_loan_out(loan, now)


def list_member_loans(
    db: Session, member_id: int, now: datetime, status: Optional[LoanStatus] = None
) -> List[LoanOut]:
    """A member's loans ordered by id, optionally filtered by computed status; 404 if member missing."""
    member = db.get(Member, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")

    loans = sorted(member.loans, key=lambda l: l.id)
    result = [to_loan_out(l, now) for l in loans]
    if status is not None:
        result = [l for l in result if l.status == status]
    return result