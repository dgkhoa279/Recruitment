# -*- coding: utf-8 -*-

import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)


class RecruitmentCVChunk(models.Model):
    _name = "recruitment.cv.chunk"
    _description = "RAG Chunk for CV and Job Description"
    _order = "document_type, application_id, job_position_id, chunk_index"

    name = fields.Char(compute="_compute_labels", store=True)
    citation = fields.Char(compute="_compute_labels", store=True, index=True)
    document_type = fields.Selection(
        [("cv", "CV"), ("job_description", "Job Description")],
        required=True,
        default="cv",
        index=True,
    )
    application_id = fields.Many2one(
        "recruitment.application",
        string="Đơn ứng tuyển",
        ondelete="cascade",
        index=True,
    )
    job_position_id = fields.Many2one(
        "recruitment.job.position",
        string="Vị trí Tuyển dụng",
        ondelete="cascade",
        required=True,
        index=True,
    )
    chunk_index = fields.Integer(required=True, index=True)
    chunk_text = fields.Text(required=True)
    chunk_vector_json = fields.Text(required=True)
    page_number = fields.Integer(default=1)
    source_checksum = fields.Char(required=True, index=True)
    text_checksum = fields.Char(required=True, index=True)
    embedding_provider = fields.Selection(
        [("ollama", "Ollama")],
        required=True,
        index=True,
    )
    embedding_model = fields.Char(required=True, index=True)
    embedding_dimensions = fields.Integer(required=True, index=True)

    @api.depends(
        "document_type",
        "application_id",
        "job_position_id",
        "page_number",
        "chunk_index",
    )
    def _compute_labels(self):
        for record in self:
            if record.document_type == "cv":
                candidate = record.application_id.candidate_id.name or record.application_id.name or "CV"
                record.name = f"CV · {candidate} · Chunk {record.chunk_index}"
                record.citation = (
                    f"CV:APP-{record.application_id.id}:P{record.page_number}:C{record.chunk_index}"
                )
            else:
                job = record.job_position_id.name or "JD"
                record.name = f"JD · {job} · Chunk {record.chunk_index}"
                record.citation = f"JD:JOB-{record.job_position_id.id}:C{record.chunk_index}"

    @api.constrains("document_type", "application_id", "job_position_id")
    def _check_source(self):
        for record in self:
            if record.document_type == "cv" and not record.application_id:
                raise ValidationError("CV chunk bắt buộc phải có application_id.")
            if (
                record.application_id
                and record.application_id.job_position_id != record.job_position_id
            ):
                raise ValidationError("Application và Job Position của chunk không đồng nhất.")

    def init(self):
        """Use pgvector when the DBA has enabled it; keep JSON as portable fallback."""
        self.env.cr.execute("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        if self.env.cr.fetchone()[0]:
            self.env.cr.execute(
                "ALTER TABLE recruitment_cv_chunk "
                "ADD COLUMN IF NOT EXISTS embedding_vector vector"
            )
        else:
            _logger.info(
                "pgvector extension chưa được bật; RAG dùng JSON cosine cho tập dữ liệu nhỏ."
            )

    def _pgvector_available(self):
        self.env.cr.execute(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_name = 'recruitment_cv_chunk'
                   AND column_name = 'embedding_vector'
            )
            """
        )
        return bool(self.env.cr.fetchone()[0])

    def _store_pgvector(self, vector):
        self.ensure_one()
        if not self._pgvector_available():
            return False
        if len(vector) != self.embedding_dimensions:
            raise ValidationError("Số chiều vector không khớp metadata của chunk.")
        vector_literal = "[" + ",".join(str(float(value)) for value in vector) + "]"
        self.env.cr.execute(
            "UPDATE recruitment_cv_chunk SET embedding_vector = %s::vector WHERE id = %s",
            (vector_literal, self.id),
        )
        return True

    @api.model
    def _cron_reindex_ai_documents(self):
        """Small batches keep Odoo responsive while rebuilding embedding spaces."""
        service = self.env["recruitment.ai.service"]
        jobs = self.env["recruitment.job.position"].sudo().search(
            [("rag_index_status", "=", "pending")], limit=2
        )
        for job in jobs:
            service.rag_index_job_position(job)
        applications = self.env["recruitment.application"].sudo().search(
            [("rag_index_status", "=", "pending"), ("cv_file", "!=", False)],
            limit=2,
        )
        for application in applications:
            service.rag_index_cv(application)
        return True
