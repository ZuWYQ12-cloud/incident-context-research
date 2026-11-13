"""Local read-only dashboard for the single-service monitor."""
import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from http_incident_summary import summarize_http


def snapshot(database, evidence_root, stale_seconds=30):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        state = dict(db.execute('SELECT * FROM state WHERE id=1').fetchone())
        settings = json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
        last = db.execute('SELECT * FROM checks ORDER BY id DESC LIMIT 1').fetchone()
        checks = [dict(r) for r in db.execute('SELECT * FROM checks ORDER BY id DESC LIMIT 20')]
        rows = db.execute('''SELECT o.incident_id, o.confirmed_at AS opened_at,
            o.first_bad_at, (SELECT MAX(r.confirmed_at) FROM events r
            WHERE r.incident_id=o.incident_id AND r.event_type='recovered') AS recovered_at
            FROM events o WHERE o.event_type='incident_opened' ORDER BY o.id DESC LIMIT 50''').fetchall()
    last = dict(last) if last else None
    age = None
    if last:
        age = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(last['checked_at'])).total_seconds())
    incidents = []
    for row in rows:
        item = dict(row)
        identifier = item['incident_id']
        item['evidence'] = None
        if len(identifier) == 32 and all(c in '0123456789abcdef' for c in identifier):
            path = Path(evidence_root) / identifier / 'manifest.json'
            if path.is_file():
                try:
                    manifest = json.loads(path.read_text(encoding='utf-8'))
                    item['evidence'] = {
                        'status': manifest.get('status'),
                        'limitation': manifest.get('limitation'),
                        'observations': [{k: obs.get(k) for k in
                            ('url', 'http_status', 'error', 'started_at', 'collected_at', 'body_truncated')}
                            for obs in manifest.get('observations', [])],
                    }
                except (OSError, ValueError, TypeError, AttributeError):
                    item['evidence'] = {'status': 'unreadable', 'observations': []}
        item['diagnosis'] = summarize_http(item['evidence'])
        incidents.append(item)
    return {'url': settings['url'], 'state': state['status'], 'last_check': last,
            'stale': age is None or age > stale_seconds, 'age_seconds': age,
            'checks': checks, 'incidents': incidents}


PAGE = '''<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Наблюдение за сервисом</title>
<style>
body{margin:0;background:#f0f3f8;color:#182638;font:16px system-ui,sans-serif}main{max-width:1100px;margin:auto;padding:32px 20px}h1{margin:8px 0}h2{font-size:20px}p{line-height:1.5}.muted{color:#53667c}.card{background:white;border:1px solid #dbe2eb;border-radius:14px;padding:22px;margin:18px 0}.badge{display:inline-block;padding:8px 14px;border-radius:8px;background:#e8edf4;font-weight:650}.healthy{background:#d9f5e5;color:#145a36}.incident{background:#ffe1e0;color:#8f2925}.stale{background:#fff0cb;color:#795500}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;border-bottom:1px solid #e5eaf0;padding:12px 8px;vertical-align:top;overflow-wrap:anywhere}.scroll{overflow-x:auto}details{border-top:1px solid #e5eaf0;padding:16px 0}summary{cursor:pointer;font-weight:600}.error{color:#a52c25}code{overflow-wrap:anywhere}button{padding:10px 16px;border:0;border-radius:8px;background:#204ea4;color:white;cursor:pointer}
</style><main>
<div class="muted">Локальный мониторинг · прототип</div><h1>Наблюдение за сервисом</h1>
<p class="muted">Доступность и материалы инцидентов. Причина сбоя автоматически не установлена.</p>
<button id="refresh">Обновить</button> <span id="refresh-time" class="muted"></span>
<p id="error" class="error" role="alert"></p>
<section class="card"><span id="status" class="badge">Загрузка…</span><p id="url"></p><p id="last" class="muted"></p><p id="note"></p></section>
<section class="card"><h2>Инциденты</h2><p class="muted">Последние 50. Время показывается в часовом поясе браузера.</p><div id="incidents"></div></section>
<section class="card"><h2>Последние проверки</h2><div class="scroll"><table><thead><tr><th>Время</th><th>HTTP</th><th>Результат</th></tr></thead><tbody id="checks"></tbody></table></div></section>
<p class="muted">Страница только отображает данные. Монитор запускается отдельно. Обновление каждые 5 секунд.</p>
</main><script>
const $=id=>document.getElementById(id);
const time=v=>v?new Date(v).toLocaleString('ru-RU'):'—';
function el(tag,text){const e=document.createElement(tag);e.textContent=text;return e;}
async function refresh(){
 try{
  const response=await fetch('/api/status',{cache:'no-store'});
  if(!response.ok)throw Error('Не удалось прочитать данные мониторинга');
  const data=await response.json();$('error').textContent='';
  $('status').className='badge '+(data.stale?'stale':data.state);
  $('status').textContent=data.stale?'Данные устарели':({healthy:'Сервис доступен',incident:'Открыт инцидент',unknown:'Состояние уточняется'}[data.state]||data.state);
  $('url').textContent=data.url;
  $('last').textContent='Последняя проверка: '+time(data.last_check?.checked_at);
  $('note').textContent=data.stale?'Свежих проверок нет. Проверьте, запущен ли монитор. Сохранённое состояние не подтверждает текущую доступность.':'Статус подтверждается серией проверок: 3 неудачных для сбоя, 2 успешных для восстановления.';
  $('refresh-time').textContent='Обновлено '+new Date().toLocaleTimeString('ru-RU');
  $('checks').replaceChildren();
  for(const check of data.checks){const tr=document.createElement('tr');for(const value of [time(check.checked_at),check.http_status??'нет ответа',check.error||(check.ok?'Успешно':'Неуспешный HTTP-ответ')])tr.append(el('td',value));$('checks').append(tr);}
  const opened=new Set([...$('incidents').querySelectorAll('details[open]')].map(e=>e.dataset.id));
  $('incidents').replaceChildren();
  if(!data.incidents.length)$('incidents').append(el('p','Инциденты пока не зарегистрированы.'));
  for(const incident of data.incidents){
   const box=document.createElement('details');box.dataset.id=incident.incident_id;box.open=opened.has(incident.incident_id);
   box.append(el('summary',(incident.recovered_at?'Восстановлен':'Открыт')+' · '+time(incident.opened_at)));
   box.append(el('p','Идентификатор: '+incident.incident_id));
   box.append(el('p','Первый неуспешный замер: '+time(incident.first_bad_at)+' · Восстановление подтверждено: '+time(incident.recovered_at)));
   const diagnosis=incident.diagnosis;
   if(diagnosis){
    const panel=document.createElement('section');panel.className='card';
    panel.append(el('h3',diagnosis.title));panel.append(el('p',diagnosis.method));
    panel.append(el('h4','Подтверждено сохранёнными наблюдениями'));
    if(!diagnosis.facts.length)panel.append(el('p','Доступных HTTP-наблюдений нет.'));
    for(const fact of diagnosis.facts){
     panel.append(el('p','['+fact.reference+'] '+fact.url+' · '+time(fact.collected_at)+' · '+fact.meaning));
     if(fact.error)panel.append(el('code',fact.error));
    }
    for(const [title,items] of [['Что остаётся неизвестным и ограничения',diagnosis.unknowns],['Следующие проверки',diagnosis.checks]]){
     panel.append(el('h4',title));const list=document.createElement('ul');
     for(const text of items)list.append(el('li',text));panel.append(list);
    }
    box.append(panel);
   }
   if(!incident.evidence)box.append(el('p','HTTP-материалы не найдены.'));
   else{
    const evidence=incident.evidence;
    box.append(el('p','Сбор материалов: '+({completed:'завершён',collecting:'в процессе',failed:'ошибка',unreadable:'не удалось прочитать'}[evidence.status]||evidence.status)));
    if(evidence.limitation)box.append(el('p',evidence.limitation));
    for(const obs of evidence.observations){box.append(el('p',obs.url+' · HTTP '+(obs.http_status??'нет ответа')+' · '+(obs.error||'Ответ получен')+' · собрано '+time(obs.collected_at)));}
   }
   $('incidents').append(box);
  }
 }catch(error){$('error').textContent=error.message;$('status').className='badge stale';$('status').textContent='Нет актуальных данных';}
}
$('refresh').onclick=refresh;refresh();setInterval(refresh,5000);
</script></html>'''


def handler(database, evidence_root, stale_seconds):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                body, content_type, status = PAGE.encode('utf-8'), 'text/html; charset=utf-8', 200
            elif self.path == '/api/status':
                try:
                    data = snapshot(database, evidence_root, stale_seconds)
                    body, status = json.dumps(data, ensure_ascii=False).encode('utf-8'), 200
                except (sqlite3.Error, OSError, ValueError, TypeError):
                    body, status = b'{"error":"monitor data unavailable"}', 503
                content_type = 'application/json; charset=utf-8'
            else:
                body, content_type, status = b'Not found', 'text/plain', 404
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8081)
    parser.add_argument('--stale-seconds', type=float, default=30)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('База мониторинга не найдена')
    if args.stale_seconds <= 0 or not 1 <= args.port <= 65535:
        parser.error('Некорректный порт или порог устаревания')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(args.database, args.evidence_dir, args.stale_seconds))
    print(f'Откройте http://localhost:{args.port} · Ctrl+C для остановки', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
