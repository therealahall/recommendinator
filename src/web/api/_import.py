import logging
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from src.config.service import auto_enrich_enabled
from src.ingestion.import_templates import (
    TemplatesUnavailable,
    available_templates,
    find_template,
    read_template,
)
from src.ingestion.importers.base import ImporterError
from src.ingestion.importers.registry import IMPORTERS, get_importer
from src.ingestion.importers.service import decode_import_text, import_file
from src.models.content import ContentType, get_enum_value
from src.utils.text import exception_for_log
from src.web.auth import CurrentUser
from src.web.guards import RequiredConfig, RequiredStorage
from src.web.responses import SurrogateSafeResponse

logger = logging.getLogger(__name__)

router = APIRouter()


class ImporterResponse(BaseModel):
    """``import-formats`` emits this key set field for field."""

    name: str
    display_name: str
    description: str
    #: False where the format itself decides, as a book-site export does.
    requires_content_type: bool


class ImportTemplateResponse(BaseModel):
    """``import-template`` emits this key set field for field."""

    importer: str
    content_type: str
    filename: str


class ImportResponse(BaseModel):
    """The ``import`` command's ``--format json`` emits this key set field for
    field, so neither interface may add, drop or rename one alone.
    """

    importer: str
    content_type: str | None
    filename: str | None
    added: int
    updated: int
    unchanged: int
    skipped: int
    failed: int
    total_rows: int
    errors: list[str]
    notes: list[str]


@router.get("/importers", response_model=list[ImporterResponse])
def list_importers() -> list[ImporterResponse]:
    """List every import format, in the order the picker offers them."""
    return [
        ImporterResponse(
            name=importer.name,
            display_name=importer.display_name,
            description=importer.description,
            requires_content_type=not importer.content_types,
        )
        for importer in IMPORTERS
    ]


@router.get("/import/templates", response_model=list[ImportTemplateResponse])
def list_import_templates() -> list[ImportTemplateResponse]:
    """List every template this install ships, keyed as the picker is."""
    try:
        templates = available_templates()
    except TemplatesUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return [
        ImportTemplateResponse(
            importer=template.importer,
            content_type=template.content_type,
            filename=template.filename,
        )
        for template in templates
    ]


@router.get("/import/templates/download")
def download_import_template(
    importer: str = Query(..., description="Import format name"),
    content_type: str = Query(
        ..., description="Content type (book, movie, tv_show, video_game)"
    ),
) -> Response:
    """Both parameters are looked up as dictionary keys, never joined onto a path."""
    try:
        template = find_template(importer, content_type)
    except TemplatesUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    if template is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No template for that import format and content type. "
                "GET /api/import/templates lists the ones this install ships."
            ),
        )

    return SurrogateSafeResponse(
        content=read_template(template),
        media_type=template.media_type,
        headers={"Content-Disposition": f'attachment; filename="{template.filename}"'},
    )


@router.post("/import", response_model=ImportResponse)
def import_upload(
    storage: RequiredStorage,
    config: RequiredConfig,
    user: CurrentUser,
    file: UploadFile,
    importer: Annotated[str, Form(description="Import format name")],
    content_type: Annotated[str | None, Form()] = None,
) -> ImportResponse:
    """No source, no cadence, no sync run, and no importer opens a path. Starlette
    spools a part over ``spool_max_size`` into the temp directory, unnamed and
    gone when the form closes.
    """
    chosen = get_importer(importer)
    if chosen is None:
        offered = ", ".join(candidate.name for candidate in IMPORTERS)
        raise HTTPException(
            status_code=400, detail=f"Unknown import format. Valid options: {offered}"
        )

    resolved_type = None
    if content_type:
        try:
            resolved_type = ContentType.from_string(content_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    try:
        result = import_file(
            storage,
            user["id"],
            decode_import_text(file.file.read()),
            chosen,
            resolved_type,
            mark_for_enrichment=auto_enrich_enabled(config),
        )
    except ImporterError as error:
        logger.info(
            "[IMPORT] %s refused the file: %s", chosen.name, exception_for_log(error)
        )
        # Answered verbatim, unlike the source-config refusals: the message
        # quotes the operator's own file, which is what makes it actionable.
        raise HTTPException(status_code=400, detail=str(error)) from error

    return ImportResponse(
        importer=result.importer,
        content_type=(
            get_enum_value(result.content_type) if result.content_type else None
        ),
        filename=file.filename,
        added=result.added,
        updated=result.updated,
        unchanged=result.unchanged,
        skipped=result.skipped,
        failed=result.failed,
        total_rows=result.total_rows,
        errors=result.errors,
        notes=result.notes,
    )
