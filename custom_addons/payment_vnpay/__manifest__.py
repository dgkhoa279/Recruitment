# -*- coding: utf-8 -*-
{
    'name': 'VNPAY Payment Provider',
    'version': '1.0.0',
    'category': 'Accounting/Payment Providers',
    'summary': 'Tích hợp Cổng thanh toán VNPAY (QR / ATM / Quốc tế)',
    'description': """
Module Thanh toán VNPAY Độc Lập cho Odoo:
- Hỗ trợ tạo URL Checkout bảo mật mã hóa HMAC-SHA512 checksum.
- Tự động tiếp nhận Return URL và IPN callback xác nhận thanh toán.
- Kết nối trực tiếp với Framework Payment Provider của Odoo.
    """,
    'author': 'Recruitment Dev Team',
    'website': 'https://vnpay.vn',
    'depends': ['payment'],
    'data': [
        'data/payment_provider_data.xml',
        'views/payment_vnpay_templates.xml',
        'views/payment_provider_views.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'payment_vnpay/static/src/js/payment_vnpay.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
