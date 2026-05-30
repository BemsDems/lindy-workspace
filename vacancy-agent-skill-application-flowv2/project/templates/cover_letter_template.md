Здравствуйте!

Меня зовут {{ candidate.name }}, я рассматриваю позицию {{ candidate.position }}.

Меня заинтересовала вакансия "{{ vacancy.title }}" в компании {{ vacancy.company }}.

{% if matched_requirements %}
С моим опытом хорошо совпадают следующие требования вакансии:
{% for req in matched_requirements %}
- {{ req }}
{% endfor %}
{% endif %}

{% if missing_requirements %}
По отдельным требованиям, которые не указаны в моём профиле, я готов дополнительно разобраться и уточнить детали на собеседовании.
{% endif %}

{{ candidate.experience_summary }}

Буду рад обсудить, чем могу быть полезен вашей команде.

С уважением,
{{ candidate.name }}
{% if candidate.contact_lines %}
{% for key, value in candidate.contact_lines.items() %}{{ key }}: {{ value }}
{% endfor %}
{% endif %}
