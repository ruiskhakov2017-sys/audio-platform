from django.http import HttpResponse

def root_view(request):
    html = """
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><title>Backend</title></head>
    <body style="font-family:sans-serif; padding:2rem;">
    <h1>Backend работает</h1>
    <p><a href="/admin/">Админ-панель</a></p>
    <p><a href="/api/">API</a></p>
    </body>
    </html>
    """
    return HttpResponse(html)
