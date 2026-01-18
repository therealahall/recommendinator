# Database Schema Design

## Current Approach vs Proposed Approach

### Current Approach (Single Table)
- Single `content_items` table with generic `metadata` JSON field
- `author` column (only relevant for books)
- All type-specific data in JSON metadata

**Pros:**
- Simple queries across all content types
- Easy to add new content types
- Flexible metadata storage

**Cons:**
- No type safety for type-specific fields
- Can't easily query type-specific fields (e.g., "find games by developer")
- `author` column is NULL for non-books (wasted space)

### Proposed Approach (Base + Type-Specific Tables)

**Base Table: `content_items`**
- Common fields: id, external_id, title, content_type, status, rating, review, date_completed
- Removed: `author` (moved to book-specific table)
- Removed: `metadata` JSON (replaced with type-specific tables)

**Type-Specific Tables:**
- `book_details` - author, pages, isbn, isbn13, publisher, year_published
- `movie_details` - director, runtime, release_year, genre, studio
- `tv_show_details` - creators, seasons, episodes, network, release_year
- `video_game_details` - developer, publisher, platform, genre, release_year

**Pros:**
- Type-safe schema for each content type
- Can query type-specific fields efficiently
- Better data integrity
- Clear separation of concerns

**Cons:**
- More complex queries (requires JOINs)
- More tables to manage
- Migration needed for existing data

## Recommended Approach

**Hybrid: Base Table + Type-Specific Tables + Metadata JSON**

Keep the base table for common fields, add type-specific tables for important fields we'll query, and keep a small metadata JSON for less-important fields.

### Schema Design

```sql
-- Base table (common fields)
content_items (
    id INTEGER PRIMARY KEY,
    external_id TEXT,
    title TEXT NOT NULL,
    content_type TEXT NOT NULL,
    status TEXT NOT NULL,
    rating INTEGER,
    review TEXT,
    date_completed DATE,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)

-- Book-specific details
book_details (
    content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id),
    author TEXT,
    pages INTEGER,
    isbn TEXT,
    isbn13 TEXT,
    publisher TEXT,
    year_published INTEGER,
    metadata TEXT  -- JSON for less-common fields
)

-- Movie-specific details
movie_details (
    content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id),
    director TEXT,
    runtime INTEGER,  -- minutes
    release_year INTEGER,
    genre TEXT,
    studio TEXT,
    metadata TEXT
)

-- TV Show-specific details
tv_show_details (
    content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id),
    creators TEXT,  -- comma-separated or JSON array
    seasons INTEGER,
    episodes INTEGER,
    network TEXT,
    release_year INTEGER,
    metadata TEXT
)

-- Video Game-specific details
video_game_details (
    content_item_id INTEGER PRIMARY KEY REFERENCES content_items(id),
    developer TEXT,
    publisher TEXT,
    platform TEXT,  -- or JSON array for multiple platforms
    genre TEXT,
    release_year INTEGER,
    metadata TEXT
)
```

## Migration Strategy

1. **Schema Version 2**: Add type-specific tables
2. Migrate existing data from JSON metadata to type-specific tables
3. Keep metadata JSON for backward compatibility initially
4. Eventually remove metadata JSON once all data is migrated

## Implementation Considerations

- Use foreign keys with ON DELETE CASCADE
- Create indexes on commonly queried fields (author, director, developer, etc.)
- Storage manager needs to handle saving to both base and type-specific tables
- Queries need to JOIN when filtering by type-specific fields
