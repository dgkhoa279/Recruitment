from odoo import models, fields, api
from odoo.exceptions import ValidationError


class JobPosition(models.Model):
    _name = "recruitment.job.position"
    _description = "Job Position"

    name = fields.Char(
        string="Job Position",
        required=True,
    )

    description = fields.Text(
        string="Description",
    )

    quantity = fields.Integer(
        string="Target Quantity",
        default=1,
    )

    hired_quantity = fields.Integer(
        string="Hired Quantity",
        compute="_compute_hired_quantity",
        store=True,
    )

    application_ids = fields.One2many(
        "recruitment.application",
        "job_position_id",
        string="Applications",
    )

    deadline = fields.Date(
        string="Deadline",
    )

    active = fields.Boolean(
        string="Active",
        default=True,
    )

    required_skills = fields.Char(
        string="Required Skills",
        help="Danh sách kỹ năng / từ khóa bắt buộc (ví dụ: Python, Odoo, HTML, CSS, SQL)",
    )

    min_experience_years = fields.Float(
        string="Min Experience (Years)",
        default=0.0,
    )

    employment_type = fields.Selection(
        [
            ("full_time", "Toàn thời gian (Full-time)"),
            ("part_time", "Bán thời gian (Part-time)"),
            ("contract", "Hợp đồng (Contract)"),
            ("internship", "Thực tập (Internship)"),
        ],
        string="Employment Type",
        default="full_time",
        required=True,
    )

    workplace_type = fields.Selection(
        [
            ("on_site", "Tại văn phòng (On-site)"),
            ("remote", "Làm từ xa (Remote)"),
            ("hybrid", "Linh hoạt (Hybrid)"),
        ],
        string="Workplace Type",
        default="on_site",
        required=True,
    )

    job_level = fields.Selection(
        [
            ("intern", "Intern / Thực tập sinh"),
            ("junior", "Junior"),
            ("middle", "Middle"),
            ("senior", "Senior"),
            ("lead", "Team Lead / Quản lý chuyên môn"),
            ("manager", "Manager / Trưởng phòng"),
        ],
        string="Job Level",
        default="middle",
        required=True,
    )

    salary_min = fields.Float(
        string="Minimum Salary (VNĐ)",
        default=0.0,
    )

    salary_max = fields.Float(
        string="Maximum Salary (VNĐ)",
        default=0.0,
    )

    hr_responsible_id = fields.Many2one(
        "res.users",
        string="HR Responsible",
        default=lambda self: self.env.user,
    )

    department_id = fields.Many2one(
        "hr.department",
        string="Phòng ban / Khối",
        help="Phòng ban trực thuộc của vị trí tuyển dụng này",
    )

    state = fields.Selection(
        [
            ("draft", "Nháp"),
            ("recruiting", "Đang tuyển dụng"),
            ("closed", "Đã đóng tuyển dụng"),
        ],
        string="Trạng thái tuyển dụng",
        default="recruiting",
        required=True,
    )

    def action_set_recruiting(self):
        for record in self:
            record.write({"state": "recruiting", "active": True})

    def action_set_closed(self):
        for record in self:
            record.write({"state": "closed"})

    def action_set_draft(self):
        for record in self:
            record.write({"state": "draft"})

    facebook_post_id = fields.Char(
        string="Facebook Post ID",
        readonly=True,
        copy=False,
    )
    facebook_post_url = fields.Char(
        string="Facebook Post Link",
        readonly=True,
        copy=False,
    )
    facebook_post_date = fields.Datetime(
        string="Facebook Post Date",
        readonly=True,
        copy=False,
    )

    def action_post_to_facebook(self):
        self.ensure_one()
        import requests
        import logging

        _logger = logging.getLogger(__name__)

        page_id = self.env['ir.config_parameter'].sudo().get_param('recruitment.facebook_page_id', default='')
        access_token = self.env['ir.config_parameter'].sudo().get_param('recruitment.facebook_page_access_token', default='')

        if not page_id or not access_token:
            raise ValidationError(
                "Chưa cấu hình Facebook Page ID hoặc Page Access Token!\n\n"
                "Vui lòng vào Menu 'Tuyển dụng -> Cấu hình -> Cài đặt' (hoặc System Parameters) để nhập thông tin kết nối Facebook."
            )

        level_dict = dict(self._fields['job_level'].selection)
        emp_dict = dict(self._fields['employment_type'].selection)
        work_dict = dict(self._fields['workplace_type'].selection)

        salary_str = "Thỏa thuận"
        if self.salary_min > 0 and self.salary_max > 0:
            salary_str = f"{self.salary_min:,.0f} - {self.salary_max:,.0f} VNĐ"
        elif self.salary_min > 0:
            salary_str = f"Từ {self.salary_min:,.0f} VNĐ"
        elif self.salary_max > 0:
            salary_str = f"Đến {self.salary_max:,.0f} VNĐ"

        message_parts = [f"🔥 [GÓC TUYỂN DỤNG] - VỊ TRÍ: {self.name.upper()}\n"]
        if self.department_id:
            message_parts.append(f"🏢 Phòng ban / Khối: {self.department_id.name}")
        message_parts.append(f"📍 Hình thức: {emp_dict.get(self.employment_type, '')} ({work_dict.get(self.workplace_type, '')})")
        message_parts.append(f"💼 Cấp bậc: {level_dict.get(self.job_level, '')}")
        message_parts.append(f"💰 Mức lương: {salary_str}")

        if self.required_skills:
            message_parts.append(f"📌 Kỹ năng yêu cầu: {self.required_skills}")
        if self.min_experience_years > 0:
            message_parts.append(f"⭐ Kinh nghiệm tối thiểu: {self.min_experience_years} năm")
        if self.deadline:
            message_parts.append(f"⏰ Hạn nộp hồ sơ: {fields.Date.to_string(self.deadline)}")

        if self.description:
            desc_clean = self.description.strip()
            if len(desc_clean) > 400:
                desc_clean = desc_clean[:400] + "..."
            message_parts.append(f"\n📋 MÔ TẢ CÔNG VIỆC:\n{desc_clean}")

        company_email = (self.env.company.email or "").strip()
        if not company_email or any(dummy in company_email for dummy in ["yourcompany.com", "example.com"]):
            company_email = "vodangkhoa270904@gmail.com"

        message_parts.append(f"\n📩 Nộp CV ngay bằng cách gửi về email: {company_email}")
        message_parts.append(f"#tuyendung #recruitment #{self.job_level} #odoo")

        full_message = "\n".join(message_parts)

        # Tự động xác định ID chuẩn của Trang từ Page Access Token qua GET /me
        target_page_id = page_id.strip()
        try:
            me_res = requests.get(f"https://graph.facebook.com/v19.0/me?access_token={access_token.strip()}", timeout=10)
            if me_res.status_code == 200:
                me_json = me_res.json()
                if me_json.get('id'):
                    target_page_id = me_json['id']
                    # Tự động cập nhật lại System Parameter cho chính xác
                    self.env['ir.config_parameter'].sudo().set_param('recruitment.facebook_page_id', target_page_id)
        except Exception as e:
            _logger.warning("Không thể tự động lấy Page ID từ GET /me: %s", str(e))

        url = f"https://graph.facebook.com/v19.0/{target_page_id}/feed"
        payload = {
            'message': full_message,
            'access_token': access_token.strip(),
        }

        try:
            response = requests.post(url, data=payload, timeout=15)
            res_json = response.json()

            if response.status_code in (200, 201) and 'id' in res_json:
                post_id = res_json['id']
                post_url = f"https://facebook.com/{post_id}"
                self.write({
                    'facebook_post_id': post_id,
                    'facebook_post_url': post_url,
                    'facebook_post_date': fields.Datetime.now(),
                })
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Đăng bài Facebook Thành công! 🎉',
                        'message': f'Tin tuyển dụng vị trí {self.name} đã được đăng lên Facebook Fanpage!',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                err_msg = res_json.get('error', {}).get('message', response.text)
                _logger.error("Lỗi khi đăng bài Facebook Graph API: %s", response.text)
                raise ValidationError(f"Facebook Graph API trả về lỗi:\n\n{err_msg}")
        except Exception as e:
            if isinstance(e, ValidationError):
                raise e
            _logger.exception("Không thể kết nối đến Facebook Graph API")
            raise ValidationError(f"Không thể kết nối đến Facebook Graph API: {str(e)}")

    _sql_constraints = [
        ("unique_name", "unique(name)", "Tên vị trí này đã tồn tại !"),
    ]

    @api.depends("application_ids.stage_id", "application_ids.active")
    def _compute_hired_quantity(self):
        hired_stage = self.env["recruitment.stage"].search(
            [("name", "=ilike", "hired")], limit=1
        )
        for record in self:
            if hired_stage:
                record.hired_quantity = len(
                    record.application_ids.filtered(
                        lambda a: a.stage_id.id == hired_stage.id
                    )
                )
            else:
                record.hired_quantity = 0

    @api.constrains("quantity")
    def _check_quantity(self):
        for record in self:
            if record.quantity <= 0:
                raise ValidationError("Số lượng tuyển dụng phải lớn hơn 0 !")

    @api.constrains("deadline")
    def _check_deadline(self):
        for record in self:
            if record.deadline and record.deadline < fields.Date.today():
                raise ValidationError("Ngày hết hạn phải lớn hơn ngày hiện tại !")

    @api.constrains("salary_min", "salary_max")
    def _check_salary_range(self):
        for record in self:
            if (
                record.salary_min > 0
                and record.salary_max > 0
                and record.salary_max < record.salary_min
            ):
                raise ValidationError("Lương tối đa không được nhỏ hơn lương tối thiểu!")

    def action_recommend_top_candidates(self):
        """
        Computes Vector Cosine Similarity between Job Requirements Vector and all Candidate CV Vectors in Database.
        Returns Top 10 matching applications instantly!
        """
        self.ensure_one()
        ai_service = self.env["recruitment.ai.service"]
        job_text = f"{self.name} {self.required_skills or ''} {self.description or ''}"
        job_vec = ai_service.generate_vector_embedding(job_text)

        app_model = self.env["recruitment.application"]
        applications = app_model.search([("active", "=", True), ("cv_file", "!=", False)])
        if not applications:
            raise ValidationError("Không tìm thấy Đơn ứng tuyển nào có file CV trong hệ thống để so sánh Vector!")

        import json

        results = []
        for app in applications:
            if app.cv_vector_json:
                try:
                    cv_vec = json.loads(app.cv_vector_json)
                except Exception:
                    cv_vec = []
            else:
                cv_vec = []

            if not cv_vec:
                cv_text = f"{app.matched_skills or ''} {app.cv_extracted_text or ''}"
                cv_vec = ai_service.generate_vector_embedding(cv_text)
                app.write({"cv_vector_json": json.dumps(cv_vec)})

            match_pct = ai_service.compute_cosine_similarity(job_vec, cv_vec)
            app.write({"matching_score": match_pct})
            results.append((app.id, match_pct))

        results.sort(key=lambda x: x[1], reverse=True)
        top_app_ids = [r[0] for r in results[:10]]

        return {
            "name": f"Top Ứng viên Phù hợp nhất cho {self.name} (Vector Search)",
            "type": "ir.actions.act_window",
            "res_model": "recruitment.application",
            "view_mode": "tree,kanban,form",
            "domain": [("id", "in", top_app_ids)],
            "context": {"order": "matching_score desc"},
        }