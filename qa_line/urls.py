from django.urls import re_path
from qa_line.views import CheckQualityLine, render_qa_page, render_report_page

'''
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
'''

urlpatterns = [
    re_path(r'^$', render_qa_page, name='qa-page'),
    re_path(r'^check/$', CheckQualityLine.as_view(), name='qa-line'),
    re_path(r'^report', render_report_page, name='qa-report'),
]
