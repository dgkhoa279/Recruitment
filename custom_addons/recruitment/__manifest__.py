# -*- coding: utf-8 -*-
{
    'name': "Recruitment",
    'summary': "Recruitment management module",
    'description': """
Recruitment management module for job positions and applicants.
    """,

    'author': "My Company",
    'website': "https://www.yourcompany.com",
    'category': 'Human Resources',
    'version': '0.1',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/stage_data.xml',
        'data/mail_template_data.xml',
        'views/views.xml',
        'views/templates.xml',
        'views/dashboard_views.xml',
        'views/candidate_views.xml',
        'views/job_position_views.xml',
        'views/application_views.xml',
        'views/stage_views.xml',
        'views/interview_views.xml',
        'views/offer_views.xml',
        'views/refuse_reason_views.xml',
        'wizard/refuse_wizard_views.xml',
        'wizard/interview_wizard_views.xml',
        'wizard/offer_wizard_views.xml',
    ],
    'demo': [
        'demo/demo.xml',
    ],
    'application': True,
    'installable': True,
}
