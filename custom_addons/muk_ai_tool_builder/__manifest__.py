{
    'name': 'MuK AI Tool Builder',
    'summary': 'Policy-controlled, AI-generated stored script tools',
    'description': """
        Lets MuK AI agents author, store, dry-run, revise and execute restricted
        Python tools. Capabilities are derived from source code while model and
        field access is enforced dynamically from the execution user's Odoo ACLs
        and record rules.
    """,
    'version': '17.0.6.8.1',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'author': 'Recruitment Dev',
    'depends': [
        'muk_ai_skills',
        'recruitment',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/security.xml',
        'data/skill_migration.xml',
        'data/skill.xml',
        'views/skill_views.xml',
        'views/generated_tool_views.xml',
        'views/tool_execution_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
