from django.http import HttpResponse

def root_view(request):
    html = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EroticAudio — Backend</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Philosopher:wght@400;700&family=Montserrat:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: #000814;
      color: #fafafa;
      font-family: 'Montserrat', sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      background: radial-gradient(ellipse 80% 50% at 50% -20%, rgba(0,180,216,0.12), transparent);
      pointer-events: none;
    }
    .wrap { position: relative; z-index: 1; text-align: center; max-width: 420px; }
    h1 {
      font-family: 'Philosopher', serif;
      font-size: 1.75rem;
      font-weight: 700;
      color: #fff;
      margin: 0 0 0.5rem;
    }
    .sub {
      color: rgba(255,255,255,0.5);
      font-size: 0.9rem;
      margin-bottom: 2rem;
    }
    .links { display: flex; flex-direction: column; gap: 0.75rem; }
    a {
      display: block;
      padding: 1rem 1.5rem;
      border-radius: 9999px;
      font-weight: 600;
      text-decoration: none;
      transition: transform 0.2s, box-shadow 0.2s;
    }
    a:hover { transform: scale(1.02); }
    .admin {
      background: #00B4D8;
      color: #000;
      box-shadow: 0 0 30px rgba(0,180,216,0.4);
    }
    .admin:hover { box-shadow: 0 0 40px rgba(0,180,216,0.5); }
    .api {
      background: rgba(255,255,255,0.08);
      color: #fafafa;
      border: 1px solid rgba(255,255,255,0.15);
    }
    .api:hover { background: rgba(255,255,255,0.12); border-color: rgba(255,255,255,0.25); }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>EroticAudio</h1>
    <p class="sub">Backend работает</p>
    <div class="links">
      <a href="/admin/" class="admin">Админ-панель</a>
      <a href="/api/" class="api">API</a>
    </div>
  </div>
</body>
</html>
    """
    return HttpResponse(html)
