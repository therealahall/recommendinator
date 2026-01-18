"""Goodreads CSV export parser."""

from typing import Iterator
import csv
from datetime import datetime
from pathlib import Path

from src.models.content import ContentItem, ContentType, ConsumptionStatus


def parse_goodreads_csv(file_path: Path) -> Iterator[ContentItem]:
    """Parse Goodreads library export CSV file.

    Args:
        file_path: Path to the Goodreads CSV export file

    Yields:
        ContentItem objects for each book in the export
    """
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            # Extract key fields
            title = row.get("Title", "").strip()
            if not title:
                continue

            author = row.get("Author", "").strip() or None

            # Parse rating (0 means unrated/unread)
            rating_str = row.get("My Rating", "0").strip()
            try:
                rating = int(rating_str) if rating_str and rating_str != "0" else None
            except ValueError:
                rating = None

            # Parse status from Exclusive Shelf
            shelf = row.get("Exclusive Shelf", "").strip().lower()
            if shelf == "read":
                status = ConsumptionStatus.COMPLETED
            elif shelf == "currently-reading":
                status = ConsumptionStatus.CURRENTLY_CONSUMING
            else:  # to-read or empty
                status = ConsumptionStatus.UNREAD

            # Parse date read
            date_read_str = row.get("Date Read", "").strip()
            date_completed = None
            if date_read_str:
                try:
                    # Goodreads format: YYYY/MM/DD
                    date_completed = datetime.strptime(date_read_str, "%Y/%m/%d").date()
                except ValueError:
                    pass

            # Extract review
            review = row.get("My Review", "").strip() or None

            # Extract additional metadata
            metadata = {
                "book_id": row.get("Book Id", "").strip(),
                "isbn": row.get("ISBN", "").strip() or None,
                "isbn13": row.get("ISBN13", "").strip() or None,
                "pages": row.get("Number of Pages", "").strip() or None,
                "year_published": row.get("Year Published", "").strip() or None,
                "publisher": row.get("Publisher", "").strip() or None,
            }

            yield ContentItem(
                id=metadata.get("book_id"),
                title=title,
                author=author,
                content_type=ContentType.BOOK,
                rating=rating,
                review=review,
                status=status,
                date_completed=date_completed,
                metadata=metadata,
            )
