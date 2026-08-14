import re
import html
from email.utils import parseaddr


class ApplicationEmailParser:

    @classmethod
    def clean_html(cls, html_content):
        if not html_content:
            return ''
        # Thay thế các thẻ xuống dòng HTML bằng kí tự newline \n
        text = re.sub(r'(?i)<(br|p|div|tr|li)\b[^>]*>', '\n', html_content)
        # Loại bỏ các thẻ HTML còn lại
        text = re.sub(r'<[^>]+>', '', text)
        # Unescape HTML entities (&nbsp;, &lt;, etc.)
        return html.unescape(text)

    @classmethod
    def parse(cls, msg_dict):
        """
        Parse email ứng tuyển.

        Trả về:
            dict: dữ liệu ứng tuyển nếu hợp lệ
            None: nếu email không phải ứng tuyển
        """

        subject = (msg_dict.get('subject') or '').strip()
        raw_body = (msg_dict.get('body') or '').strip()
        email_from = (msg_dict.get('email_from') or msg_dict.get('from') or '').strip()

        # ---------------------------------------------------------
        # 1. Kiểm tra Subject (không phân biệt hoa thường)
        # ---------------------------------------------------------
        match_subj = re.search(r'\[APPLICATION\]\s*(.*)', subject, re.IGNORECASE)
        if not match_subj:
            return None

        job_position = match_subj.group(1).strip()

        # ---------------------------------------------------------
        # 2. Làm sạch Body (chuyển HTML sang plain text)
        # ---------------------------------------------------------
        body_text = cls.clean_html(raw_body)

        # ---------------------------------------------------------
        # 3. Trích xuất các trường dữ liệu
        # ---------------------------------------------------------
        data = {}

        # Họ tên
        name_match = re.search(r'(?:Họ\s*tên|Họ\s*và\s*tên|Name)\s*:\s*(.+)', body_text, re.IGNORECASE)
        if name_match:
            data['name'] = name_match.group(1).strip()
        else:
            sender_name, sender_email = parseaddr(email_from)
            data['name'] = sender_name or (sender_email.split('@')[0] if sender_email else 'Ứng viên mới')

        # Email
        email_match = re.search(r'(?:Email|Mail)\s*:\s*(.+)', body_text, re.IGNORECASE)
        if email_match:
            data['email'] = email_match.group(1).strip()
        else:
            sender_name, sender_email = parseaddr(email_from)
            data['email'] = sender_email or ''

        # Phone
        phone_match = re.search(r'(?:Số\s*điện\s*thoại|SĐT|Phone)\s*:\s*(.+)', body_text, re.IGNORECASE)
        if phone_match:
            phone_raw = phone_match.group(1).strip()
            phone_digits = ''.join(c for c in phone_raw if c.isdigit())
            data['phone'] = phone_digits if phone_digits else '0000000000'
        else:
            data['phone'] = '0000000000'

        # Vị trí ứng tuyển
        if not job_position:
            pos_match = re.search(r'(?:Vị\s*trí\s*ứng\s*tuyển|Vị\s*trí|Job)\s*:\s*(.+)', body_text, re.IGNORECASE)
            if pos_match:
                job_position = pos_match.group(1).strip()
            else:
                job_position = 'Vị trí tuyển dụng chung'

        # ---------------------------------------------------------
        # 4. Kiểm tra CV attachment
        # ---------------------------------------------------------
        attachments = msg_dict.get('attachments') or []
        cv_attachment = None

        for attachment in attachments:
            if not attachment or not attachment[0]:
                continue
            filename = str(attachment[0]).lower()
            if filename.endswith(('.pdf', '.doc', '.docx', '.png', '.jpg', '.jpeg')):
                cv_attachment = attachment
                break

        return {
            'name': data['name'],
            'email': data['email'],
            'phone': data['phone'],
            'job_position': job_position,
            'cv_attachment': cv_attachment,
        }