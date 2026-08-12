from odoo.tests.common import TransactionCase

from ..models.application_email_parser import ApplicationEmailParser


class TestApplicationEmailParser(TransactionCase):

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

    def test_multiple_applications_different_cvs(self):
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