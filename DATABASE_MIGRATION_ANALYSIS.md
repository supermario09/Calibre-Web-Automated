# CWA Database Integration Analysis: PostgreSQL Migration Feasibility

## Executive Summary

Calibre-Web Automated uses **three separate SQLite databases** with a deeply integrated mix of SQLAlchemy ORM (for metadata.db and app.db) and raw SQLite3 (for cwa.db). The architecture is **tightly coupled to SQLite-specific features** (PRAGMA statements, COLLATE NOCASE, WAL mode, attachment strategy), making a PostgreSQL migration a **medium-to-high complexity undertaking**.

**Estimated Effort**: 60-100 hours of development and testing.

---

## Part 1: How Deeply is SQLite Integrated?

### Integration Depth: ⚠️ DEEP

SQLite is integrated across multiple layers:

1. **Connection Strategy**: Uses SQLite's ATTACH feature to access two databases (metadata.db + app.db) via a single connection
2. **Isolation Level**: Requires `SERIALIZABLE` isolation for consistency
3. **Locking Strategy**: Pessimistic (SQLite locks entire database, not rows)
4. **Concurrency Model**: Write-serialization via file locks + WAL mode for read concurrency
5. **Schema Flexibility**: Case-insensitive collation heavily relied upon
6. **Query Patterns**: PRAGMA statements for schema inspection and performance tuning

---

## Part 2: Database Initialization Code

### Location: `cps/__init__.py` → `cps/db.py` → `cps/ub.py`

#### Initialization Flow:
```python
# cps/__init__.py (create_app function)
calibre_db = db.CalibreDB()  # Instantiate metadata.db handler
ub.init_db(cli_param.settings_path)  # Initialize app.db session
calibre_db.init_db()  # Open metadata.db connection with attachment
```

#### app.db Initialization (cps/ub.py:1239)
```python
def init_db(app_db_path):
    global session, app_DB_path
    app_DB_path = app_db_path
    
    engine = create_engine(
        'sqlite:///{0}'.format(app_db_path),
        echo=False,
        connect_args={'timeout': 30}  # SQLite timeout (ms)
    )
    
    Session = scoped_session(sessionmaker())
    Session.configure(bind=engine)
    session = Session()
    
    # Health check + schema creation
    Base.metadata.create_all(engine)
    migrate_Database(session)  # Handle schema migrations
```

**Key Details**:
- Creates **scoped_session** for request-local thread safety
- Timeout set to 30 seconds (SQLite connection blocking)
- Uses **declarative_base** ORM pattern (SQLAlchemy)
- Automatic schema creation on first run

#### metadata.db Initialization (cps/db.py:748)
```python
@classmethod
def setup_db(cls, config_calibre_dir, app_db_path):
    # ... validation code ...
    
    cls.engine = create_engine(
        'sqlite://',  # In-memory engine (stores in RAM)
        echo=False,
        isolation_level="SERIALIZABLE",  # SQLite-specific ⚠️
        connect_args={'check_same_thread': False, 'timeout': 30},
        poolclass=StaticPool  # Single connection, no pooling
    )
    
    with cls.engine.begin() as connection:
        # Attach both databases to single connection
        connection.execute(text("attach database '{}' as calibre;".format(dbpath)))
        connection.execute(text("attach database '{}' as app_settings;".format(app_db_path)))
        
        # Enable WAL mode unless on network share
        if not os.getenv('NETWORK_SHARE_MODE', 'False').lower() in ('1', 'true'):
            connection.execute(text("PRAGMA calibre.journal_mode=WAL"))
            connection.execute(text("PRAGMA app_settings.journal_mode=WAL"))
```

**Critical Architecture Decision**:
- Uses **SQLite ATTACH** syntax to access both metadata.db and app.db from **one connection**
- PostgreSQL doesn't support ATTACH - would require separate connections or cross-database queries
- This is the **biggest migration blocker**

---

## Part 3: ORM/SQLAlchemy Configuration

### Declarative Base

Both databases use SQLAlchemy's declarative ORM:
```python
# cps/db.py
from sqlalchemy.orm import declarative_base
Base = declarative_base()

# cps/ub.py
from sqlalchemy.orm import declarative_base
Base = declarative_base()
```

### Connection Pooling

**metadata.db** (db.py):
```python
poolclass=StaticPool  # Single connection, no pooling
connect_args={
    'check_same_thread': False,  # Allow cross-thread access
    'timeout': 30  # 30 second connection timeout
}
isolation_level="SERIALIZABLE"  # Lock entire database
```

**app.db** (ub.py):
```python
# Uses default pooling (NullPool for SQLite)
connect_args={'timeout': 30}  # 30 second timeout
```

### Session Management

**Global scoped_session pattern**:
```python
Session = scoped_session(sessionmaker())
session = Session()  # Thread-local variable
```

**Thread-local initialization** (background tasks):
```python
def init_db_thread():
    """Called in background service threads"""
    engine = create_engine('sqlite:///{0}'.format(app_DB_path),
                           connect_args={'timeout': 30})
    Session = scoped_session(sessionmaker())
    Session.configure(bind=engine)
    return Session()
```

---

## Part 4: The Three Databases - Complete Schema

### Database 1: metadata.db (Calibre Library)

**ORM**: Full SQLAlchemy  
**Tables**: ~20 core + dynamic custom_columns  
**Managed By**: SQLAlchemy via `db.CalibreDB`  

#### Core Tables (Calibre Standard):
```python
# Books and related entities
class Library_Id(Base):
    __tablename__ = 'library_id'

class Books(Base):
    __tablename__ = 'books'
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(collation='NOCASE'), nullable=False)  # ⚠️ NOCASE
    author_sort = Column(String(collation='NOCASE'))
    # ... 20+ more columns

class Authors(Base):
    __tablename__ = 'authors'
    name = Column(String(collation='NOCASE'), unique=True)  # ⚠️ NOCASE

class Tags(Base):
    __tablename__ = 'tags'
    name = Column(String(collation='NOCASE'), unique=True)  # ⚠️ NOCASE

class Series(Base):
    __tablename__ = 'series'
    name = Column(String(collation='NOCASE'), unique=True)  # ⚠️ NOCASE

class Publishers(Base):
    __tablename__ = 'publishers'
    name = Column(String(collation='NOCASE'), unique=True)  # ⚠️ NOCASE

class Languages(Base):
    __tablename__ = 'languages'
    lang_code = Column(String(collation='NOCASE'), unique=True)  # ⚠️ NOCASE

class Identifiers(Base):
    __tablename__ = 'identifiers'
    type = Column(String(collation='NOCASE'))  # ISBN, DOI, etc.
    val = Column(String(collation='NOCASE'))

class Ratings(Base):
    __tablename__ = 'ratings'
    rating = Column(Integer, CheckConstraint('rating>-1 AND rating<11'))

class Comments(Base):
    __tablename__ = 'comments'
    text = Column(String(collation='NOCASE'))

class Data(Base):  # Book file formats
    __tablename__ = 'data'
    format = Column(String(collation='NOCASE'), nullable=False)  # EPUB, MOBI, PDF, etc.

class Metadata_Dirtied(Base):  # Tracks dirty metadata
    __tablename__ = 'metadata_dirtied'

# Link tables (many-to-many)
books_authors_link = Table('books_authors_link', Base.metadata,
    Column('book', Integer, ForeignKey('books.id'), primary_key=True),
    Column('author', Integer, ForeignKey('authors.id'), primary_key=True)
)
books_tags_link = Table('books_tags_link', Base.metadata, ...)
books_series_link = Table('books_series_link', Base.metadata, ...)
books_ratings_link = Table('books_ratings_link', Base.metadata, ...)
books_languages_link = Table('books_languages_link', Base.metadata, ...)
books_publishers_link = Table('books_publishers_link', Base.metadata, ...)
```

#### Dynamic Custom Columns:
```python
# Each custom column in Calibre generates up to 3 tables:
# 1. custom_column_<id>
# 2. books_custom_column_<id>_link
# 3. custom_column_<id> relationships

# Setup done at runtime via db.py:633-680
for row in custom_columns:
    # Creates new declarative classes dynamically
    cc_classes[row.id] = type(str('custom_column_' + str(row.id)), (Base,), ...)
```

---

### Database 2: app.db (Flask Settings)

**ORM**: Full SQLAlchemy  
**Tables**: ~35 (24 core + 6 OAuth + 5 Koreader)  
**Managed By**: SQLAlchemy via `ub` module  

#### User & Settings (8 tables):
```python
class User(UserBase, Base):
    __tablename__ = 'user'
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True)
    email = Column(String(120), unique=True)
    role = Column(SmallInteger)  # Permission flags
    password = Column(String)
    view_settings = Column(JSON, default={})  # ⚠️ JSON storage
    theme = Column(Integer, default=1)
    auto_send_enabled = Column(Boolean)
    wireless_device_ip = Column(String(256))
    hardcover_token = Column(String, unique=True)

class _Settings(Base):  # Global Calibre-Web settings
    __tablename__ = 'settings'
    # 50+ configuration columns

class OAuthProvider(Base):
    __tablename__ = 'oauthProvider'
    # OAuth endpoints, credentials

class User_Sessions(Base):
    __tablename__ = 'user_session'
    user_id = Column(Integer, ForeignKey('user.id'))
    session_key = Column(String)
    random = Column(String)
    expiry = Column(Integer)

class Registration(Base):
    __tablename__ = 'registration'
    domain = Column(String)
    allow = Column(Integer)

class RemoteAuthToken(Base):
    __tablename__ = 'remote_auth_token'
    auth_token = Column(String, unique=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    verified = Column(Boolean)
    expiration = Column(DateTime)
    token_type = Column(Integer)

class Downloads(Base):
    __tablename__ = 'downloads'
    book_id = Column(Integer)
    user_id = Column(Integer, ForeignKey('user.id'))
```

#### Shelving (7 tables):
```python
class Shelf(Base):
    __tablename__ = 'shelf'
    id = Column(Integer, primary_key=True)
    uuid = Column(String, default=lambda: str(uuid.uuid4()))
    name = Column(String)
    is_public = Column(Integer, default=0)
    user_id = Column(Integer, ForeignKey('user.id'))
    kobo_sync = Column(Boolean, default=False)

class MagicShelf(Base):
    __tablename__ = 'magic_shelf'
    name = Column(String)
    rules = Column(JSON, default={})  # ⚠️ JSON storage
    is_system = Column(Boolean, default=False)
    user_id = Column(Integer, ForeignKey('user.id'))

class MagicShelfCache(Base):
    __tablename__ = 'magic_shelf_cache'
    book_ids = Column(JSON)  # ⚠️ JSON storage

class HiddenMagicShelfTemplate(Base):
    __tablename__ = 'hidden_magic_shelf_templates'
    template_key = Column(String)
    shelf_id = Column(Integer, ForeignKey('magic_shelf.id'))

class DismissedDuplicateGroup(Base):
    __tablename__ = 'dismissed_duplicate_groups'
    group_hash = Column(String(32))
    user_id = Column(Integer, ForeignKey('user.id'))

class BookShelf(Base):
    __tablename__ = 'book_shelf_link'
    book_id = Column(Integer)
    shelf = Column(Integer, ForeignKey('shelf.id'))

class ShelfArchive(Base):
    __tablename__ = 'shelf_archive'
    uuid = Column(String)
    user_id = Column(Integer, ForeignKey('user.id'))
```

#### Reading State & Kobo Sync (9 tables):
```python
class ReadBook(Base):
    __tablename__ = 'book_read_link'
    book_id = Column(Integer)
    user_id = Column(Integer, ForeignKey('user.id'))
    read_status = Column(Integer)  # 0=unread, 1=finished, 2=in_progress

class Bookmark(Base):
    __tablename__ = 'bookmark'
    user_id = Column(Integer, ForeignKey('user.id'))
    book_id = Column(Integer)
    bookmark_key = Column(String)

class ArchivedBook(Base):
    __tablename__ = 'archived_book'
    user_id = Column(Integer, ForeignKey('user.id'))
    book_id = Column(Integer)
    is_archived = Column(Boolean)

class KoboSyncedBooks(Base):
    __tablename__ = 'kobo_synced_books'
    user_id = Column(Integer, ForeignKey('user.id'))
    book_id = Column(Integer)

class KoboReadingState(Base):
    __tablename__ = 'kobo_reading_state'
    user_id = Column(Integer, ForeignKey('user.id'))
    book_id = Column(Integer)
    priority_timestamp = Column(DateTime)

class KoboBookmark(Base):
    __tablename__ = 'kobo_bookmark'
    kobo_reading_state_id = Column(Integer, ForeignKey('kobo_reading_state.id'))

class KoboStatistics(Base):
    __tablename__ = 'kobo_statistics'
    # Reading progress tracking

class KoboAnnotationSync(Base):
    __tablename__ = 'kobo_annotation_sync'
    # Annotation sync state

class HardcoverBookBlacklist(Base):
    __tablename__ = 'hardcover_book_blacklist'
```

#### Additional Tables (5):
```python
class Thumbnail(Base):
    __tablename__ = 'thumbnail'
    entity_id = Column(Integer)
    filename = Column(String)
    generated_at = Column(DateTime)

class OAuth(OAuthConsumerMixin, Base):
    __tablename__ = 'oauth'
    provider_user_id = Column(String(256))
    user_id = Column(Integer, ForeignKey('user.id'))

class BookFormatChecksum(Base):  # progress_syncing module
    __tablename__ = 'book_format_checksums'

class KOSyncProgress(Base):  # progress_syncing module
    __tablename__ = 'kosync_progress'

class GdriveIds(Base):
    __tablename__ = 'gdrive_ids'
```

---

### Database 3: cwa.db (CWA Custom Features)

**ORM**: None - Raw SQLite3 with wrapper class  
**Access Pattern**: `scripts/cwa_db.py:CWA_DB` class  
**Tables**: ~10  

#### Direct SQL Example (cwa_db.py:39):
```python
class CWA_DB:
    def __init__(self, verbose=False):
        self.db_file = "cwa.db"
        self.db_path = "/config/"
        self.con, self.cur = self.connect_to_db()  # sqlite3.Connection, sqlite3.Cursor
        
    def connect_to_db(self) -> tuple[sqlite3.Connection, sqlite3.Cursor]:
        con = sqlite3.connect(self.db_path + self.db_file, timeout=30)
        cur = con.cursor()
        return con, cur
    
    def make_tables(self):
        """Execute raw SQL from cwa_schema.sql"""
        with open(self.schema_path, 'r') as f:
            schema = f.read()
        tables = schema.split(';')
        for table in tables:
            self.cur.execute(table)  # ⚠️ Raw SQL
            self.con.commit()
```

#### Schema Tables (scripts/cwa_schema.sql):

**Audit Logs**:
```sql
CREATE TABLE IF NOT EXISTS cwa_enforcement(
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,  -- ⚠️ AUTOINCREMENT
    timestamp TEXT NOT NULL,
    book_id INTEGER NOT NULL,
    book_title TEXT NOT NULL,
    author TEXT NOT NULL,
    file_path TEXT NOT NULL,
    trigger_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cwa_import(
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    timestamp TEXT NOT NULL,
    filename TEXT NOT NULL,
    original_backed_up TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cwa_conversions(
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    timestamp TEXT NOT NULL,
    filename TEXT NOT NULL,
    original_format TEXT NOT NULL,
    original_backed_up TEXT NOT NULL,
    end_format TEXT DEFAULT "" NOT NULL
);

CREATE TABLE IF NOT EXISTS epub_fixes(
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    timestamp TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    fixes_applied TEXT DEFAULT ""
);
```

**Settings (Single Row)**:
```sql
CREATE TABLE IF NOT EXISTS cwa_settings(
    default_settings SMALLINT DEFAULT 1 NOT NULL,
    auto_backup_imports SMALLINT DEFAULT 1 NOT NULL,
    auto_backup_conversions SMALLINT DEFAULT 1 NOT NULL,
    auto_zip_backups SMALLINT DEFAULT 1 NOT NULL,
    cwa_update_notifications SMALLINT DEFAULT 1 NOT NULL,
    -- ... 60+ feature flags ...
    auto_convert SMALLINT DEFAULT 1 NOT NULL,
    auto_convert_target_format TEXT DEFAULT "epub" NOT NULL,
    auto_convert_ignored_formats TEXT DEFAULT "" NOT NULL,
    auto_metadata_enforcement SMALLINT DEFAULT 1 NOT NULL,
    kindle_epub_fixer SMALLINT DEFAULT 1 NOT NULL,
    koreader_sync_enabled SMALLINT DEFAULT 0 NOT NULL,
    -- ... and 50+ more settings ...
);
```

**Duplicate Detection & Resolution**:
```sql
CREATE TABLE IF NOT EXISTS cwa_duplicate_cache(
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Singleton table
    scan_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    duplicate_groups_json TEXT,  -- ⚠️ JSON serialization
    total_count INTEGER DEFAULT 0,
    scan_pending INTEGER DEFAULT 1,
    scan_duration_seconds REAL DEFAULT 0,
    scan_method_used TEXT DEFAULT 'python'
);

CREATE TABLE IF NOT EXISTS cwa_duplicate_resolutions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    group_hash TEXT NOT NULL,
    kept_book_id INTEGER NOT NULL,
    deleted_book_ids TEXT NOT NULL,  -- JSON array
    strategy TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    backed_up INTEGER DEFAULT 1,
    user_id INTEGER,
    notes TEXT
);
CREATE INDEX idx_duplicate_resolutions_timestamp ON cwa_duplicate_resolutions(timestamp);
CREATE INDEX idx_duplicate_resolutions_group_hash ON cwa_duplicate_resolutions(group_hash);
```

**Scheduled Jobs** (APScheduler Persistence):
```sql
CREATE TABLE IF NOT EXISTS cwa_scheduled_jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    job_type TEXT NOT NULL,
    book_id INTEGER,
    user_id INTEGER,
    scheduler_job_id TEXT DEFAULT '',
    run_at_utc TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'scheduled',
    last_error TEXT DEFAULT ''
);
```

**User Activity**:
```sql
CREATE TABLE IF NOT EXISTS cwa_user_activity(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    user_name TEXT,
    event_type TEXT,
    item_id INTEGER,
    item_title TEXT,
    extra_data TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_activity_user ON cwa_user_activity(user_id);
Create INDEX idx_activity_event ON cwa_user_activity(event_type);
CREATE INDEX idx_activity_time ON cwa_user_activity(timestamp);
```

---

## Part 5: Database Usage Patterns

### Pattern 1: SQLAlchemy ORM (metadata.db + app.db)

**Reading** (db.py):
```python
def get_book(self, book_id):
    self.ensure_session()
    return self.session.query(Books).filter(Books.id == book_id).first()

def get_author_by_name(self, name):
    self.ensure_session()
    return self.session.query(Authors).filter(Authors.name == name).first()
```

**Writing** (ub.py):
```python
def update_download(book_id, user_id):
    check = session.query(Downloads).filter(Downloads.user_id == user_id).filter(Downloads.book_id == book_id).first()
    if not check:
        new_download = Downloads(user_id=user_id, book_id=book_id)
        session.add(new_download)
        session.commit()  # ⚠️ May throw OperationalError
```

**Exception Handling**:
```python
try:
    session.commit()
except (exc.OperationalError, exc.InvalidRequestError) as e:
    session.rollback()
    log.error_or_exception(e)
```

### Pattern 2: Raw SQLite3 (cwa.db)

**Wrapper Class Usage** (cwa_functions.py:1453):
```python
cwa_db.cur.execute("""
    INSERT INTO cwa_enforcement(timestamp, book_id, book_title, author, file_path, trigger_type)
    VALUES(?, ?, ?, ?, ?, ?)
""", (timestamp, book_id, title, author, path, trigger_type))
cwa_db.con.commit()
```

**Direct Connection** (cover_enforcer.py:122):
```python
con = sqlite3.connect("/config/app.db", timeout=60)
cur = con.cursor()
split_library = cur.execute('SELECT config_calibre_split FROM settings;').fetchone()[0]
con.close()
```

**Multi-Table Operations** (ingest_processor.py:200+):
```python
def _load_cps_settings_from_app_db():
    app_db_path = os.environ.get("CALIBRE_DBPATH", "/config")
    engine = create_engine('sqlite:///{}'.format(app_db_path),
                          connect_args={'timeout': 30})
    Session = scoped_session(sessionmaker())
    Session.configure(bind=engine)
    # Use session or fall back to raw SQLite3
```

### Pattern 3: Mixed ORM + Raw SQL

**Fine Example** (cwa_functions.py:884):
```python
# ORM: Get session from ub module
from . import ub  # Uses SQLAlchemy

# Raw SQL: Access cwa.db directly
def get_cwa_stats():
    cwa_db = CWA_DB()
    cwa_db.cur.execute("SELECT COUNT(*) FROM cwa_import")
    return cwa_db.cur.fetchone()[0]
```

---

## Part 6: Transaction Management & PRAGMA Usage

### Transaction Isolation

**SQLAlchemy Level**:
```python
# metadata.db: SERIALIZABLE (pessimistic)
isolation_level="SERIALIZABLE"

# app.db: Default SQLite (pessimistic, but less restrictive)
```

**PRAGMA Commands** (db.py:780-790):
```python
# Enable WAL mode for concurrency
connection.execute(text("PRAGMA calibre.journal_mode=WAL"))  # ⚠️ SQLite-specific
connection.execute(text("PRAGMA app_settings.journal_mode=WAL"))

# Connection pooling
connection.execute(text("PRAGMA busy_timeout=5000"))  # ⚠️ SQLite-specific

# Schema inspection
cols = conn.execute(text("PRAGMA calibre.table_info(books)")).fetchall()  # ⚠️ SQLite-specific
```

### Lock Handling in Scripts

**Ingest Processor** (ingest_processor.py:30-100):
```python
class ProcessLock:
    """Robust process lock using file locking"""
    def acquire(self, timeout=5):
        fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # ⚠️ No equivalent needed in PostgreSQL (row-level locking)
    
    def _check_stale_lock(self):
        """Check if holding process still exists"""
        os.kill(holding_pid, 0)
        # ⚠️ Manual lock management (PostgreSQL advisory locks are cleaner)
```

---

## Part 7: Migration Complexity Assessment

### COLLATE NOCASE Complexity: 🔴 CRITICAL

**Affected Columns**:
```
metadata.db:
- Books.title, Books.author_sort, Books.sort
- Authors.name, Authors.sort
- Tags.name
- Series.name, Series.sort
- Publishers.name, Publishers.sort
- Languages.lang_code
- Identifiers.type, Identifiers.val
- Comments.text
- Data.format

app.db:
- User.name, User.email
- (25+ more columns in various tables)
```

**Migration Path**:
1. **Option A**: Use PostgreSQL `citext` extension
   ```sql
   CREATE EXTENSION IF NOT EXISTS citext;
   ALTER TABLE authors ALTER COLUMN name TYPE citext;
   ```
   - Pro: Automatic case-insensitive behavior
   - Con: Extension dependency

2. **Option B**: Add LOWER() to all queries
   ```sql
   -- Before: WHERE name = 'Author Name'
   -- After: WHERE LOWER(name) = LOWER('Author Name')
   ```
   - Pro: No dependencies
   - Con: Everywhere, ~100+ search/replace operations

**Estimated Effort**: 10-15 hours

### ATTACH Strategy Complexity: 🔴 CRITICAL

**Current SQLite Approach**:
```python
# Single connection, both DBs attached
connection.execute(text("attach database '{}' as calibre;".format(dbpath)))
connection.execute(text("attach database '{}' as app_settings;".format(app_db_path)))

# Queries use schema prefixes: SELECT * FROM calibre.books
```

**PostgreSQL Alternative**:
```python
# Option 1: Two separate connections
calibre_engine = create_engine('postgresql://.../<calibre_db>')
app_engine = create_engine('postgresql://.../<app_db>')

# Option 2: Same connection, two schemas
single_conn = create_engine('postgresql://.../<shared_db>')
# SELECT * FROM calibre.books, app_settings.downloads
```

**Migration Effort**: 5-10 hours (refactor db.py:748-850)

### SQLite-Specific Syntax: 🟠 HIGH

1. **PRAGMA Statements** (~10 instances):
   - `PRAGMA journal_mode=WAL` → Connection pool configuration
   - `PRAGMA table_info(table)` → information_schema queries
   - `PRAGMA busy_timeout=5000` → Connection timeout
   - **Effort**: 3-5 hours

2. **AUTOINCREMENT** (~20 tables):
   - SQLite: `INTEGER PRIMARY KEY AUTOINCREMENT`
   - PostgreSQL: `SERIAL` or `BIGSERIAL`
   - **Effort**: 2-3 hours (automatic via schema script)

3. **Collation** (discussed above)

4. **Foreign Keys**:
   - Need explicit `PRAGMA foreign_keys=ON`
   - PostgreSQL: Enabled by default
   - **Effort**: 1-2 hours

### JSON Column Support: 🟢 LOW

Both SQLite and PostgreSQL support JSON:
```python
# SQLite: Text-based JSON storage
view_settings = Column(JSON, default={})

# PostgreSQL: Native JSONB support
# No changes needed in ORM!
```

**Effort**: <1 hour

### Raw SQLite3 Code in Scripts: 🟠 MEDIUM

**Affected Files**:
- `scripts/cwa_db.py` (~300 lines of raw SQL)
- `scripts/cover_enforcer.py` (~20 raw SQL calls)
- `scripts/ingest_processor.py` (~5 raw SQL references)

**Options**:
1. Refactor to use SQLAlchemy (preferred)
2. Switch to psycopg2 for PostgreSQL queries
3. Keep mixed ORM/raw pattern with conditional logic

**Estimated Effort**: 8-12 hours

### Custom Column Dynamics: 🟠 MEDIUM

Calibre allows custom fields that generate tables at runtime:
```python
# Calible: 10-50 custom column tables depending on library setup
for row in custom_columns:
    cc_classes[row.id] = type('custom_column_' + str(row.id), (Base,), {...})
```

**Migration Impact**:
- Schema introspection via `PRAGMA table_info` → PostgreSQL `information_schema`
- Table creation logic needs adaptation
- **Effort**: 5-8 hours

### Locking & Concurrency: 🟠 MEDIUM

**SQLite Pessimistic**:
- Entire database locked during writes
- WAL mode: readers can continue during writes
- File-level locking via `fcntl`

**PostgreSQL Optimistic**:
- Row-level locking
- MVCC (Multiple Version Concurrency Control)
- Deadlock handling needed

**Risk**: May surface race conditions in tests  
**Effort**: 5-10 hours (edge case testing + potential code fixes)

---

## Part 8: PostgreSQL Migration Strategy

### Phased Approach:

**Phase 1: app.db Migration** (Weeks 1-2)
- Create PostgreSQL target schema
- Write migration script (SQLAlchemy → psycopg2)
- Handle COLLATE NOCASE for ~15 columns
- Test user auth, OAuth, shelf sync
- Estimated: 15-20 hours

**Phase 2: metadata.db Migration** (Weeks 2-3)
- Refactor `db.CalibreDB.setup_db()` to use two connections
- Remove ATTACH usage
- Migrate custom columns schema
- Handle Books/Authors/Tags/Series queries
- Estimated: 20-25 hours

**Phase 3: cwa.db Migration** (Weeks 3-4)
- Either migrate to PostgreSQL native OR keep SQLite for audit logs
- If migrating: rewrite `cwa_db.py` with SQLAlchemy
- Update ingest_processor.py, cover_enforcer.py
- Estimated: 15-20 hours

**Phase 4: Testing & Optimization** (Weeks 4-5)
- Integration tests with real PostgreSQL
- Performance baseline vs SQLite
- WAL mode behavior → Connection pool tuning
- Production readiness checks
- Estimated: 15-20 hours

**Total**: 60-100 hours

### Connection String Format:
```python
# app.db → PostgreSQL
postgresql://user:password@hostname:5432/calibre_web_app

# metadata.db → PostgreSQL
postgresql://user:password@hostname:5432/calibre_web_metadata

# cwa.db → PostgreSQL OR SQLite (if kept local)
postgresql://user:password@hostname:5432/calibre_web_cwa
```

### Required PostgreSQL Extensions:
```sql
CREATE EXTENSION IF NOT EXISTS citext;  -- For COLLATE NOCASE replacement
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";  -- If using UUIDs
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- For text search optimization
```

---

## Part 9: Critical Gotchas

### 🔴 Gotcha 1: Duplicate Table Names
The table `hardcover_match_queue` exists in **both**:
- `app.db` (SQLAlchemy ORM)
- `cwa.db` (Raw SQLite3)

PostgreSQL schema name collision risk!  
**Fix**: Use separate schemas (`app.hardcover_match_queue` vs `cwa.hardcover_match_queue`)

### 🔴 Gotcha 2: WAL Mode Dependencies
Many tests/scripts assume WAL mode concurrency behavior:
- Readers won't block during writes
- Fast concurrent reads

PostgreSQL MVCC is different!  
**Impact**: Tests may need redesign  
**Fix**: Use `pg_stat_activity` for monitoring instead of lock files

### 🔴 Gotcha 3: PRAGMA Use Throughout
Schema inspection code everywhere:
```python
conn.execute(text("PRAGMA table_info(books)"))
```

PostgreSQL equivalent:
```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'books'
```

**Fix**: Create abstraction layer for schema inspection

### 🔴 Gotcha 4: NULL vs Empty String Semantics
SQLite treats NULL and empty string differently:
```python
# SQLite: NULL != '' (different)
# PostgreSQL: NULL != '' (same behavior, but stricter NULL checking)
```

Some queries may break!  
**Fix**: Audit all WHERE clauses for NULL handling

### 🟠 Gotcha 5: JSON Column Serialization
```python
# cwa_db stores complex objects as JSON strings
serializable_groups = json.dumps([...])  # OK in both
```

Risk: PostgreSQL validates JSON structure  
**Fix**: Ensure all JSON is valid (probably already is)

### 🟠 Gotcha 6: Custom Column Table Names
```python
# Dynamically created table names
"custom_column_42"  # OK in both
"books_custom_column_42_link"  # OK in both
```

Ensure table name length limits checked  
**Fix**: Add validation on dynamic table creation

---

## Part 10: Code Changes Required

### Change Category 1: Connection Setup (20-30% of effort)

**File**: `cps/db.py` (setup_db method, ~100 lines)

**Before**:
```python
cls.engine = create_engine('sqlite://', 
    poolclass=StaticPool,
    isolation_level="SERIALIZABLE",
    connect_args={'check_same_thread': False, 'timeout': 30})

with cls.engine.begin() as connection:
    connection.execute(text("attach database '{}' as calibre;".format(dbpath)))
    connection.execute(text("attach database '{}' as app_settings;".format(app_db_path)))
```

**After**:
```python
# Option 1: Separate engines
cls.calibre_engine = create_engine('postgresql://.../<calibre_db>')
cls.app_engine = create_engine('postgresql://.../<app_db>')

# Option 2: Two schemas in one DB
cls.engine = create_engine('postgresql://.../<shared_db>')
# Use schema prefixes: "calibre.books", "app_settings.downloads"
```

### Change Category 2: Query Adaptation (30-40% of effort)

**COLLATE NOCASE → LOWER() or citext**:

**Before** (any file using Authors.name):
```python
query.filter(Authors.name == 'Isaac Asimov')
```

**After** (Option A: citext):
```python
# Authors.name type changed to CITEXT, nothing in code changes!
query.filter(Authors.name == 'Isaac Asimov')
```

**After** (Option B: LOWER()):
```python
from sqlalchemy import func
query.filter(func.lower(Authors.name) == func.lower('Isaac Asimov'))
```

### Change Category 3: PRAGMA Removal (10-15% of effort)

**Before** (db.py:790):
```python
connection.execute(text("PRAGMA calibre.journal_mode=WAL"))
connection.execute(text("PRAGMA busy_timeout=5000"))
cols = conn.execute(text("PRAGMA calibre.table_info(books)")).fetchall()
```

**After**:
```python
# WAL mode: Configure in postgresql.conf or connection pool
# busy_timeout: Set via connection pool parameters
# PRAGMA table_info: Use information_schema
from sqlalchemy.pool import QueuePool
engine = create_engine(..., poolclass=QueuePool, pool_pre_ping=True)

# Schema inspection wrapper
def get_table_info(engine, table_name):
    inspector = inspect(engine)
    return inspector.get_columns(table_name)
```

### Change Category 4: Raw SQL Scripts (15-20% of effort)

**Before** (cover_enforcer.py:122):
```python
con = sqlite3.connect("/config/app.db", timeout=60)
cur = con.cursor()
split_library = cur.execute('SELECT config_calibre_split FROM settings;').fetchone()[0]
```

**After**:
```python
import psycopg2
con = psycopg2.connect("dbname=calibre_web_app user=... host=...")
cur = con.cursor()
cur.execute('SELECT config_calibre_split FROM settings;')
split_library = cur.fetchone()[0]
```

**Or (Preferred - use SQLAlchemy)**:
```python
from cps import ub
from cps.db import with_db_session

@with_db_session
def get_split_config():
    return ub.session.query(Settings).first().config_calibre_split
```

---

## Part 11: Testing & Validation

### Test Categories Needing Updates:

1. **Unit Tests** (~30-40% need updates)
   - Mock databases with PostgreSQL
   - Test COLLATE NOCASE behavior
   - Verify JSON column handling

2. **Integration Tests** (~50-60% need updates)
   - Real PostgreSQL container
   - Test concurrent access patterns
   - Lock/deadlock handling

3. **Schema Tests**:
   - Custom column dynamic creation
   - Migration scripts
   - Foreign key constraints

### Key Test Files:
- `tests/unit/test_calibre_init.py` - Database initialization
- `tests/integration/test_ingest_pipeline.py` - Full pipeline with DB
- `tests/smoke/test_schedule_ops_endpoints_smoke.py` - Job persistence

---

## Part 12: Estimated Implementation Timeline

| Phase | Task | Hours | User Story |
|---|---|---|---|
| 1 | PostgreSQL setup & schema design | 8-10 | "Set up 3 separate PostgreSQL DBs or shared DB with schemas" |
| 2 | Refactor `db.CalibreDB.setup_db()` for separate connections | 13-15 | "Remove ATTACH usage, use two engines" |
| 3 | Create COLLATE NOCASE abstraction | 10-12 | "Support case-insensitive queries with citext or LOWER()" |
| 4 | Rewrite `cwa_db.py` with SQLAlchemy or psycopg2 | 8-10 | "Make cwa.db use ORM or native PostgreSQL module" |
| 5 | Update `scripts/cover_enforcer.py` for PostgreSQL | 5-7 | "Switch from sqlite3 to psycopg2" |
| 6 | Update `scripts/ingest_processor.py` for PostgreSQL | 5-7 | "Switch from sqlite3 to psycopg2" |
| 7 | Refactor transaction handling (OperationalError → psycopg2.errors) | 5-8 | "Handle PostgreSQL exceptions" |
| 8 | Update unit tests for PostgreSQL mocks | 10-12 | "Mock PostgreSQL instead of SQLite" |
| 9 | Write integration tests with Docker PostgreSQL | 15-20 | "Full DB testing with real PostgreSQL" |
| 10 | Performance tuning & optimization | 10-15 | "Ensure PostgreSQL doesn't regress vs SQLite" |
| **TOTAL** | | **84-116 hours** | |

---

## Conclusion: Migration Feasibility

### ✅ Feasible: YES
The CWA codebase can be migrated to PostgreSQL, but requires:
1. Significant refactoring of connection strategy (ATTACH → separate connections)
2. COLLATE NOCASE handling throughout
3. PRAGMA removal and PostgreSQL-specific tuning
4. Rewriting ~20% of the codebase (raw SQL scripts)
5. Comprehensive testing with real PostgreSQL instances

### 📊 Complexity Matrix

```
DIFFICULTY:  ████████░░ 8/10 (High)
TIME:        ██████████ 10/10 (60-100 hours)
RISK:        ███████░░░ 7/10 (Race conditions, edge cases)
VALUE:       ██████░░░░ 6/10 (Better scalability, but adds complexity)
```

### 🎯 Recommendation

**Do it IF**:
- You need to scale beyond 500,000+ books
- You need multi-instance deployments with failover
- You have dedicated DataOps/DBA resources (10-15% ongoing)
- PostgreSQL is already your corporate standard

**Don't do it IF**:
- You're happy with SQLite's simplicity
- Single-instance deployment is sufficient
- Your team lacks PostgreSQL expertise
- You need predictable cloud costs (PostgreSQL managed services are pricier)

### 🚀 Alternative: Stay on SQLite

SQLite3 is production-ready for CWA because:
- ✅ ACID transactions
- ✅ WAL mode provides good concurrency
- ✅ No server to manage
- ✅ Backups are file copies
- ✅ Scales well to ~1M books (with proper tuning)

The PRAGMA + ATTACH architecture is actually quite clever for CWA's use case!
