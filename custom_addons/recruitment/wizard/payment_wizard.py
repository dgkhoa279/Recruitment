# -*- coding: utf-8 -*-
import datetime
from markupsafe import Markup
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class PaymentWizard(models.TransientModel):
    _name = "recruitment.payment.wizard"
    _description = "Wizard Thanh toán Đơn ứng tuyển"

    application_id = fields.Many2one(
        "recruitment.application",
        string="Đơn ứng tuyển",
        required=True,
        ondelete="cascade",
    )

    candidate_name = fields.Char(
        related="application_id.candidate_id.name",
        string="Tên Ứng viên",
        readonly=True,
    )

    candidate_email = fields.Char(
        related="application_id.candidate_id.email",
        string="Email Ứng viên",
        readonly=True,
    )

    job_position_name = fields.Char(
        related="application_id.job_position_id.name",
        string="Vị trí tuyển dụng",
        readonly=True,
    )

    amount = fields.Float(
        string="Số tiền thanh toán (VNĐ)",
        default=2000000.0,
        required=True,
    )

    provider_id = fields.Many2one(
        "payment.provider",
        string="Phương thức thanh toán",
        domain="[('state', 'in', ['enabled', 'test'])]",
        required=True,
    )

    def action_confirm_payment(self):
        """
        LUỒNG SẠCH CHUẨN 100%:
        HR Bấm nút ➔ Hệ thống sinh Link VNPAY ➔ Tự động gửi Email cho Ứng viên ➔ Đóng Wizard & Báo thành công!
        """
        self.ensure_one()
        if not self.application_id:
            raise ValidationError("Không tìm thấy đơn ứng tuyển để thanh toán!")

        provider = self.provider_id
        method_code = provider.code or "vietqr"

        app_id = self.application_id.id
        amount_int = int(self.amount)
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        ref_code = f"APP{app_id}_{now_str}"

        if method_code == "vnpay":
            base_url = provider.get_base_url()
            checkout_url = provider.vnpay_generate_checkout_url(
                amount=self.amount,
                reference=ref_code,
                order_info=f"Thanh toan don ung tuyen APP {app_id}",
                return_url=f"{base_url}/payment/vnpay/return",
            )

            # 1. Gửi Email trực tiếp cho Ứng viên
            email = self.candidate_email or (self.application_id.candidate_id.email if self.application_id.candidate_id else None)
            job_title = self.job_position_name or "Vị trí tuyển dụng"
            formatted_amount = f"{int(self.amount):,} VNĐ"

            if email:
                # Tự động lấy Email người gửi từ Outgoing Mail Server (Gmail) hoặc user hiện tại
                mail_server = self.env['ir.mail_server'].sudo().search([('active', '=', True)], limit=1)
                email_from = mail_server.smtp_user if (mail_server and mail_server.smtp_user) else (self.env.user.email or "vodangkhoa270904@gmail.com")

                subject = f"Thông báo Thanh toán Phí Xét duyệt Ứng tuyển - {job_title}"
                body = f"""
                <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 8px; padding: 24px;">
                    <h3 style="color: #005baa; margin-top: 0;">Kính gửi {self.candidate_name or 'Ứng viên'},</h3>
                    <p>Cảm ơn bạn đã nộp hồ sơ ứng tuyển vị trí <b>{job_title}</b> tại công ty chúng tôi.</p>
                    <p>Vui lòng hoàn tất thanh toán phí dịch vụ xét duyệt hồ sơ ứng tuyển theo thông tin bên dưới:</p>
                    
                    <div style="background-color: #f8f9fa; border-left: 4px solid #005baa; padding: 12px 16px; margin: 16px 0;">
                        <p style="margin: 4px 0;"><b>Số tiền thanh toán:</b> <span style="color: #d9534f; font-size: 18px; font-weight: bold;">{formatted_amount}</span></p>
                        <p style="margin: 4px 0;"><b>Phương thức:</b> Cổng Thanh toán Quốc tế / QR VNPAY</p>
                    </div>

                    <p>Quý khách vui lòng bấm vào nút bên dưới để thanh toán trực tiếp qua Cổng VNPAY:</p>
                    <div style="text-align: center; margin: 24px 0;">
                        <a href="{checkout_url}" style="background-color: #005baa; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 16px; display: inline-block;" target="_blank">Thanh toán VNPAY Ngay ➔</a>
                    </div>
                    
                    <p style="font-size: 12px; color: #777;">Nếu nút bấm trên không hoạt động, bạn có thể sao chép liên kết này vào trình duyệt:<br/><a href="{checkout_url}">{checkout_url}</a></p>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;"/>
                    <p style="font-size: 13px; color: #555; margin-bottom: 0;">Trân trọng,<br/><b>Bộ phận Tuyển dụng Doanh nghiệp</b></p>
                </div>
                """
                mail_values = {
                    "subject": subject,
                    "body_html": body,
                    "email_to": email,
                    "email_from": email_from,
                }
                self.env["mail.mail"].sudo().create(mail_values).send()

            # 2. Ghi nhật ký Chatter với Markup (không vỡ layout)
            chatter_msg = Markup(
                f"📧 <b>Đã gửi Email chứa Link Thanh toán VNPAY tới Ứng viên ({email or 'Chưa có email'}):</b><br/>"
                f"🔗 <a href='{checkout_url}' target='_blank' style='word-break: break-all;'>Mở trang Thanh toán VNPAY ➔</a>"
            )
            self.application_id.message_post(body=chatter_msg)

            # 3. Đóng Wizard & Hiển thị Thông báo Toast Notification cho HR
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Gửi Email Thanh Toán Thành Công! 🚀',
                    'message': f'Đã sinh Link VNPAY & gửi Email tới ứng viên {self.candidate_name or ""} ({email or "Không có email"}).',
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }

        else:  # VietQR / Tiền mặt tại quầy
            self.application_id.action_confirm_payment_and_hire(
                amount=self.amount,
                method=method_code,
            )
            checkout_url = f"https://img.vietqr.io/image/MB-0348786481-compact2.png?amount={amount_int}&addInfo=APP{app_id}%20FASTTRACK&accountName=RECRUITMENT%20VIP"
            return {
                "type": "ir.actions.act_url",
                "url": checkout_url,
                "target": "new",
            }
