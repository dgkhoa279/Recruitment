{
    'name': 'MuK AI - Ollama Provider',
    'summary': 'Local Ollama provider with optional Gemini fallback for MuK AI',
    'version': '17.0.1.4.2',
    'category': 'Productivity',
    'license': 'LGPL-3',
    'author': 'Recruitment Dev',
    'depends': ['muk_ai'],
    'data': [
        'data/provider.xml',
        'data/model.xml',
        'views/provider_views.xml',
    ],
    'post_init_hook': '_post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
