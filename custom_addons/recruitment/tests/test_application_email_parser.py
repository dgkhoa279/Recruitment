import base64
import datetime
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from ..models.application_email_parser import ApplicationEmailParser


class TestApplicationEmailParser(TransactionCase):

    def setUp(self):
        super().setUp()
        ai_service = self.env['recruitment.ai.service']
        provider_patch = patch.object(
            type(ai_service),
            'analyze_cv',
            return_value=None,
        )
        provider_patch.start()
        self.addCleanup(provider_patch.stop)

    def test_valid_application_email(self):

        msg_dict = {
            'subject': '[APPLICATION] Backend Developer',

            'body': """
Họ tên: Nguyễn Văn A
Email: nguyenvana@gmail.com
Số điện thoại: 0901234567
Vị trí ứng tuyển: Backend Developer
""",

            'attachments': [
                (
                    'CV_NguyenVanA.pdf',
                    b'fake-pdf-content',
                    'application/pdf'
                )
            ]
        }

        result = ApplicationEmailParser.parse(msg_dict)

        self.assertTrue(result)

        self.assertEqual(
            result['name'],
            'Nguyễn Văn A'
        )

        self.assertEqual(
            result['email'],
            'nguyenvana@gmail.com'
        )

        self.assertEqual(
            result['phone'],
            '0901234567'
        )

        self.assertEqual(
            result['job_position'],
            'Backend Developer'
        )

    def test_invalid_subject(self):

        msg_dict = {
            'subject': 'Em xin việc ạ',

            'body': """
Họ tên: Nguyễn Văn A
Email: nguyenvana@gmail.com
Số điện thoại: 0901234567
Vị trí ứng tuyển: Backend Developer
""",

            'attachments': [
                (
                    'CV.pdf',
                    b'fake',
                    'application/pdf'
                )
            ]
        }

        result = ApplicationEmailParser.parse(msg_dict)

        self.assertFalse(result)

    def test_missing_job_position(self):

        msg_dict = {
            'subject': 'Xin chao',

            'body': """
Họ tên: Nguyễn Văn A
Email: nguyenvana@gmail.com
Số điện thoại: 0901234567
""",

            'attachments': [
                (
                    'CV.pdf',
                    b'fake',
                    'application/pdf'
                )
            ]
        }

        result = ApplicationEmailParser.parse(msg_dict)

        self.assertFalse(result)

    def test_missing_cv(self):

        msg_dict = {
            'subject': '[APPLICATION] Backend Developer',

            'body': """
Họ tên: Nguyễn Văn A
Email: nguyenvana@gmail.com
Số điện thoại: 0901234567
Vị trí ứng tuyển: Backend Developer
""",

            'attachments': []
        }

        result = ApplicationEmailParser.parse(msg_dict)

        self.assertTrue(result)
        self.assertEqual(result['name'], 'Nguyễn Văn A')
        self.assertEqual(result['job_position'], 'Backend Developer')

    def test_message_new_creates_candidate_and_application(self):
        self.env['recruitment.job.position'].create({
            'name': 'Python Developer',
            'quantity': 2,
        })
        msg_dict = {
            'subject': '[APPLICATION] Python Developer',
            'body': """
Họ tên: Trần Văn B
Email: tranvanb@gmail.com
Số điện thoại: 0987654321
Vị trí ứng tuyển: Python Developer
""",
            'attachments': [
                (
                    'CV_TranVanB.pdf',
                    b'fake-pdf-content',
                    'application/pdf'
                )
            ]
        }

        application = self.env['recruitment.application'].message_new(msg_dict)
        self.assertTrue(application)
        self.assertEqual(application.candidate_id.name, 'Trần Văn B')
        self.assertEqual(application.candidate_id.email, 'tranvanb@gmail.com')
        self.assertEqual(application.candidate_id.phone, '0987654321')
        self.assertEqual(application.job_position_id.name, 'Python Developer')

        # Kiểm tra xem khi candidate đã tồn tại thì tự dùng lại Candidate cũ
        application_2 = self.env['recruitment.application'].message_new(msg_dict)
        self.assertEqual(application_2.candidate_id.id, application.candidate_id.id)

    def test_non_existent_job_position_rejected(self):
        """Kiểm tra email ứng tuyển vị trí không tồn tại trong danh mục sẽ bị từ chối hoàn toàn."""
        msg_dict = {
            'subject': '[APPLICATION] Non Existent Position 12345',
            'body': 'Họ tên: Fake Candidate\nEmail: fake@gmail.com',
            'attachments': [('CV.pdf', b'fake', 'application/pdf')]
        }
        app = self.env['recruitment.application'].message_new(msg_dict)
        self.assertIsNone(app)

        # Kiểm tra message_route cũng bỏ qua
        routes = self.env['recruitment.application'].message_route(None, msg_dict)
        self.assertEqual(routes, [])

    def test_multiple_applications_different_cvs(self):
        self.env['recruitment.job.position'].create({'name': 'Backend Developer', 'quantity': 1})
        self.env['recruitment.job.position'].create({'name': 'DevOps Engineer', 'quantity': 1})

        msg_dict_1 = {
            'subject': '[APPLICATION] Backend Developer',
            'body': 'Họ tên: Lê Văn C\nEmail: levanc@gmail.com\nSố điện thoại: 0911223344',
            'attachments': [('CV_Backend.pdf', b'content-backend', 'application/pdf')]
        }
        app_1 = self.env['recruitment.application'].message_new(msg_dict_1)

        msg_dict_2 = {
            'subject': '[APPLICATION] DevOps Engineer',
            'body': 'Họ tên: Lê Văn C\nEmail: levanc@gmail.com\nSố điện thoại: 0911223344',
            'attachments': [('CV_DevOps.pdf', b'content-devops', 'application/pdf')]
        }
        app_2 = self.env['recruitment.application'].message_new(msg_dict_2)

        # 1 Candidate duy nhất
        self.assertEqual(app_1.candidate_id.id, app_2.candidate_id.id)
        candidate = app_1.candidate_id
        self.assertEqual(len(candidate.application_ids), 2)

        # Mỗi Application lưu đúng file CV riêng biệt
        self.assertEqual(app_1.cv_filename, 'CV_Backend.pdf')
        self.assertEqual(app_2.cv_filename, 'CV_DevOps.pdf')

    def test_refuse_wizard_and_email_sending(self):
        self.env['recruitment.job.position'].create({'name': 'Tester', 'quantity': 1})
        msg_dict = {
            'subject': '[APPLICATION] Tester',
            'body': 'Họ tên: Phạm Văn D\nEmail: phamvand@gmail.com\nSố điện thoại: 0933445566',
            'attachments': [('CV_Tester.pdf', b'fake-content', 'application/pdf')]
        }
        app = self.env['recruitment.application'].message_new(msg_dict)
        self.assertTrue(app.active)

        # 1. Test Interview Action & Wizard
        res_interview = app.action_send_interview_email()
        self.assertEqual(res_interview['res_model'], 'recruitment.interview.wizard')

        future_date = fields.Datetime.now() + datetime.timedelta(days=2)
        int_wizard = self.env['recruitment.interview.wizard'].create({
            'application_id': app.id,
            'interviewer': 'Trần Văn A',
            'scheduled_date': future_date,
            'interview_type': 'online',
            'location': 'https://meet.google.com/test-meet',
        })
        int_wizard._onchange_interview_fields()
        self.assertIn('Trần Văn A', int_wizard.body_html)

        int_wizard.action_send_mail_and_create_interview()
        interviews = self.env['recruitment.interview'].search([('application_id', '=', app.id)])
        self.assertTrue(interviews)
        self.assertEqual(interviews.interviewer, 'Trần Văn A')
        self.assertEqual(interviews.location, 'https://meet.google.com/test-meet')

        # A real calendar.event is created and linked to the Interview. It is
        # then eligible for the standard Odoo -> Google Calendar synchronization.
        calendar_event = interviews.calendar_event_id
        self.assertTrue(calendar_event)
        self.assertEqual(calendar_event.start, future_date)
        self.assertEqual(
            calendar_event.stop,
            future_date + datetime.timedelta(hours=1),
        )
        self.assertEqual(calendar_event.user_id, self.env.user)
        self.assertEqual(calendar_event.location, 'https://meet.google.com/test-meet')
        self.assertEqual(
            calendar_event.videocall_location,
            'https://meet.google.com/test-meet',
        )
        self.assertIn(
            self.env.user.partner_id,
            calendar_event.attendee_ids.partner_id,
        )

        # Schedule changes and cancellation stay synchronized with Calendar.
        rescheduled_date = future_date + datetime.timedelta(hours=2)
        interviews.write({
            'scheduled_date': rescheduled_date,
            'location': 'https://meet.google.com/rescheduled-meet',
        })
        self.assertEqual(calendar_event.start, rescheduled_date)
        self.assertEqual(
            calendar_event.stop,
            rescheduled_date + datetime.timedelta(hours=1),
        )
        self.assertEqual(
            calendar_event.location,
            'https://meet.google.com/rescheduled-meet',
        )
        self.assertEqual(
            len(calendar_event.attendee_ids.filtered(
                lambda attendee: attendee.partner_id == self.env.user.partner_id
            )),
            1,
        )
        interviews.status = 'cancelled'
        self.assertFalse(calendar_event.active)

        # Verify Mail Activity created for Application
        self.assertTrue(app.activity_ids)
        self.assertIn("Phỏng vấn ứng viên", app.activity_ids[0].summary)

        # 2. Test Offer Action & Wizard
        res_offer = app.action_send_offer_email()
        self.assertEqual(res_offer['res_model'], 'recruitment.offer.wizard')

        offer_wizard = self.env['recruitment.offer.wizard'].create({
            'application_id': app.id,
            'proposed_salary': 25000000.0,
            'salary_period': 'monthly',
        })
        offer_wizard._onchange_offer_fields()
        self.assertIn('25,000,000', offer_wizard.body_html)

        offer_wizard.action_send_offer_and_create_record()
        offers = self.env['recruitment.offer'].search([('application_id', '=', app.id)])
        self.assertTrue(offers)
        self.assertEqual(offers.proposed_salary, 25000000.0)
        self.assertEqual(offers.status, 'sent')

        # 3. Test Refuse Wizard
        reason = self.env.ref('recruitment.refuse_reason_skills')
        wizard = self.env['recruitment.refuse.wizard'].create({
            'application_ids': [(6, 0, [app.id])],
            'refuse_reason_id': reason.id,
            'send_email': True,
        })
        wizard._onchange_refuse_reason_id()
        self.assertEqual(wizard.template_id.id, reason.template_id.id)

        wizard.action_refuse_reason_apply()
        self.assertFalse(app.active)
        self.assertEqual(app.refuse_reason_id.id, reason.id)

    def test_cv_pdf_skill_scanning(self):
        """Test CV PDF text extraction and skill matching score calculation."""
        job = self.env['recruitment.job.position'].create({
            'name': 'Python Skill Developer',
            'required_skills': 'Python, Odoo, HTML, CSS, PostgreSQL',
            'quantity': 1,
        })
        candidate = self.env['recruitment.candidate'].create({
            'name': 'Nguyễn Văn Skill',
            'email': 'skill.test@example.com',
            'phone': '0900000000',
            'job_position_id': job.id,
        })

        cv_text = "Experienced software engineer specializing in Python, Odoo, HTML, and CSS."
        app = self.env['recruitment.application'].create({
            'candidate_id': candidate.id,
            'job_position_id': job.id,
            'cv_file': base64.b64encode(cv_text.encode('utf-8')),
            'cv_filename': 'resume.txt',
        })

        self.assertEqual(app.matching_score, 80.0)
        self.assertIn("Python", app.matched_skills)
        self.assertIn("PostgreSQL", app.missing_skills)

    def test_hired_application_creates_one_employee(self):
        department = self.env['hr.department'].create({
            'name': 'Phòng Kỹ thuật kiểm thử',
        })
        job = self.env['recruitment.job.position'].create({
            'name': 'Backend Engineer Test',
            'department_id': department.id,
            'employment_type': 'internship',
            'required_skills': 'Python, PostgreSQL',
        })
        candidate = self.env['recruitment.candidate'].create({
            'name': 'Nguyễn Văn Hired',
            'email': 'hired.employee@example.com',
            'phone': '0901234567',
            'address': '123 Đường Kiểm Thử',
            'date_of_birth': '2000-01-02',
            'gender': 'male',
            'education': 'Kỹ thuật phần mềm',
            'skills': 'Python, SQL',
            'cv_file': base64.b64encode(b'test cv content'),
            'cv_filename': 'candidate-cv.pdf',
            'job_position_id': job.id,
        })
        application = self.env['recruitment.application'].create({
            'candidate_id': candidate.id,
            'job_position_id': job.id,
            'source': 'email',
        })

        offer_stage = self.env.ref('recruitment.stage_offer')
        hired_stage = self.env.ref('recruitment.stage_hired')
        application.stage_id = offer_stage
        self.assertFalse(application.can_invite_interview)
        with self.assertRaises(ValidationError):
            application.action_send_interview_email()

        application.stage_id = hired_stage
        employee = application.employee_id
        self.assertTrue(employee)
        self.assertFalse(application.can_send_offer)
        with self.assertRaises(ValidationError):
            application.action_send_offer_email()
        self.assertEqual(employee.name, candidate.name)
        self.assertEqual(employee.work_email, candidate.email)
        self.assertEqual(employee.private_email, candidate.email)
        self.assertEqual(employee.mobile_phone, candidate.phone)
        self.assertEqual(employee.private_phone, candidate.phone)
        self.assertEqual(employee.private_street, candidate.address)
        self.assertEqual(employee.birthday, candidate.date_of_birth)
        self.assertEqual(employee.gender, candidate.gender)
        self.assertEqual(employee.study_field, candidate.education)
        self.assertEqual(employee.department_id, department)
        self.assertEqual(employee.job_id.name, job.name)
        self.assertEqual(employee.job_id.department_id, department)
        self.assertEqual(employee.employee_type, 'trainee')
        self.assertTrue(self.env['ir.attachment'].search_count([
            ('res_model', '=', 'hr.employee'),
            ('res_id', '=', employee.id),
            ('name', '=', 'candidate-cv.pdf'),
        ]))

        employee_count = self.env['hr.employee'].search_count([
            ('work_email', '=', candidate.email),
        ])
        application.stage_id = offer_stage
        application.stage_id = hired_stage
        self.assertEqual(application.employee_id, employee)
        self.assertEqual(
            self.env['hr.employee'].search_count([
                ('work_email', '=', candidate.email),
            ]),
            employee_count,
        )
