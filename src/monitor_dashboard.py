"""Local read-only dashboard for the single-service monitor."""
import argparse
import json
import sqlite3
import secrets
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from http_incident_summary import summarize_http
from incident_log_review import review_logs
from incident_model_view import load_model_review
from model_assessment import save_assessment, VERDICTS
from incident_model_job import Jobs, job_state


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
        item['logs'] = review_logs(Path(evidence_root) / identifier, identifier) if len(identifier) == 32 and all(c in '0123456789abcdef' for c in identifier) else {'status': 'missing'}
        item['model_review'] = load_model_review(Path(evidence_root) / identifier, identifier) if len(identifier) == 32 and all(c in '0123456789abcdef' for c in identifier) else {'status': 'missing'}
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
<p class="muted">Просмотр материалов и сохранение оценки инженера. Монитор запускается отдельно. Обновление каждые 5 секунд.</p>
</main><script>
const $=id=>document.getElementById(id);
const time=v=>v?new Date(v).toLocaleString('ru-RU'):'—';
function el(tag,text){const e=document.createElement(tag);e.textContent=text;return e;}
const editedForms=new Set();
let editRevision=0;
async function refresh(){
 if(editedForms.size||document.activeElement?.closest('form'))return;
 const revision=editRevision;
 try{
  const response=await fetch('/api/status',{cache:'no-store'});
  if(!response.ok)throw Error('Не удалось прочитать данные мониторинга');
  const data=await response.json();
  if(editedForms.size||revision!==editRevision||document.activeElement?.closest('form'))return;
  $('error').textContent='';
  $('status').className='badge '+(data.stale?'stale':data.state);
  $('status').textContent=data.stale?'Данные устарели':({healthy:'Сервис доступен',incident:'Открыт инцидент',unknown:'Состояние уточняется'}[data.state]||data.state);
  $('url').textContent=data.url;
  $('last').textContent='Последняя проверка: '+time(data.last_check?.checked_at);
  $('note').textContent=data.stale?'Свежих проверок нет. Проверьте, запущен ли монитор. Сохранённое состояние не подтверждает текущую доступность.':'Статус подтверждается серией проверок: 3 неудачных для сбоя, 2 успешных для восстановления.';
  $('refresh-time').textContent='Обновлено '+new Date().toLocaleTimeString('ru-RU');
  $('checks').replaceChildren();
  for(const check of data.checks){const tr=document.createElement('tr');for(const value of [time(check.checked_at),check.http_status??'нет ответа',check.error||(check.ok?'Успешно':'Неуспешный HTTP-ответ')])tr.append(el('td',value));$('checks').append(tr);}
  const opened=new Set([...$('incidents').querySelectorAll('details[data-id][open]')].map(e=>e.dataset.id));
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
   const logs=incident.logs;
   if(logs){
    box.append(el('h3','Журналы контейнеров и разбор ошибок'));
    if(logs.status==='missing')box.append(el('p','Журналы для этого инцидента не собирались или не найдены.'));
    else if(logs.status==='unreadable')box.append(el('p','Материалы недоступны для разбора: '+logs.error));
    else{
     box.append(el('p','Окно выгрузки: '+time(logs.since)+' — '+time(logs.until)));
     for(const source of logs.sources){
      const detail=document.createElement('details');detail.dataset.id=incident.incident_id+'-'+source.service;detail.open=opened.has(detail.dataset.id);
      const labels={verified:'контрольная сумма совпадает; включён в разбор',empty:'контрольная сумма совпадает; журнал пуст',invalid:'проверка файла не пройдена',collection_failed:'ошибка выгрузки; исключён из разбора',truncated:'обрезан; исключён из разбора'};
      detail.append(el('summary',source.service+' · '+(labels[source.status]||source.status)));
      detail.append(el('p','Получен: '+time(source.collected_at)));
      if(source.error)detail.append(el('p',source.error));
      if(source.text){const pre=el('pre',source.text);pre.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;font-size:12px';detail.append(pre);}
      box.append(detail);
     }
     if(!logs.facts.length)box.append(el('p','Поддерживаемые сигнатуры не найдены в доступных для разбора журналах. Причина не установлена.'));
     for(const fact of logs.facts){
      box.append(el('h4',fact.meaning));
      for(const evidence of fact.evidence){box.append(el('p','Источник: '+evidence.observation_id));const quote=el('pre',evidence.quote);quote.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';box.append(quote);}
      box.append(el('p','Не установлено: '+fact.not_established.join(' ')));
      box.append(el('p','Следующая проверка: '+fact.next_check));
     }
     for(const note of logs.limitations)box.append(el('p',note));
    }
   }
   const model=incident.model_review;
   if(model){
    const panel=document.createElement('section');panel.className='card';
    panel.append(el('h3','Предположение модели — требует проверки'));
    if(model.status==='available'){
     panel.append(el('p',model.warning));
     panel.append(el('p','Модель: '+model.model+' · seed: '+model.seed+' · токенов ответа: '+model.output_tokens));
     panel.append(el('p','Запрос подготовлен: '+time(model.prepared_at)+' · ретроспективный разбор'));
     if(model.token_limit_reached)panel.append(el('p','Достигнут лимит генерации: ответ может быть обрезан.'));
     const answer=el('pre',model.answer);answer.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.6';panel.append(answer);
     panel.append(el('h4','Отметка инженера'));
     const assessment=model.assessment;
     if(assessment?.status==='current'){
      panel.append(el('p',assessment.label+' · '+assessment.reviewer+' · '+time(assessment.reviewed_at)));
      panel.append(el('p',assessment.note));panel.append(el('p',assessment.scope));
     }else panel.append(el('p',assessment?.message||'Ручная оценка ещё не сохранена.'));
     if(assessment?.status==='missing'){
      const form=document.createElement('form');
      const author=document.createElement('input');author.required=true;author.maxLength=200;author.autocomplete='name';
      const verdict=document.createElement('select');
      for(const [value,title] of [['uncertain','Недостаточно данных для оценки'],['issues_found','Обнаружены замечания'],['supported','Замечаний по обоснованности не обнаружено']]){const option=el('option',title);option.value=value;verdict.append(option);}
      const note=document.createElement('textarea');note.required=true;note.maxLength=10000;note.rows=5;
      for(const [title,control] of [['Автор оценки',author],['Результат проверки',verdict],['Комментарий и основания оценки',note]]){
       const label=el('label',title);label.style.display='block';control.style.cssText='display:block;box-sizing:border-box;width:100%;padding:10px;margin:6px 0 16px;font:inherit';label.append(control);form.append(label);
      }
      form.append(el('p','Если оценка подготовлена с ИИ-ассистентом, укажите это в комментарии. Сохранённая оценка не перезаписывается.'));
      const submit=el('button','Сохранить оценку');submit.type='submit';const message=el('p','');message.setAttribute('role','status');form.append(submit,message);
      const protectDraft=()=>{
       editedForms.add(form);editRevision++;
       $('refresh-time').textContent='Обновление карточек приостановлено на время заполнения оценки.';
      };
      form.addEventListener('input',protectDraft);
      form.addEventListener('change',protectDraft);
      form.onsubmit=async event=>{
       event.preventDefault();protectDraft();submit.disabled=true;
       try{
        const response=await fetch('/api/assessment',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':data.review_token},body:JSON.stringify({incident_id:incident.incident_id,reviewer:author.value,verdict:verdict.value,note:note.value})});
        const result=await response.json();if(!response.ok)throw Error(result.error||'Ошибка сохранения');
        editedForms.delete(form);editRevision++;form.remove();await refresh();
       }catch(error){message.textContent=error.message;submit.disabled=false;}
      };
      panel.append(form);
     }
    }else panel.append(el('p',model.status==='invalid'?'Ответ скрыт: '+model.error:model.status==='incomplete'?'Результат генерации ещё не сохранён полностью.':'Сохранённого ответа модели нет.'));
    box.append(panel);
   }
   if(data.model_enabled){
    const job=incident.model_job||{status:'not_started'};
    if(job.status!=='not_started')box.append(el('p','Диагностика: '+({queued:'ожидает запуска',preparing:'подготовка материалов',generating:'модель формирует ответ',completed:'завершена',failed:'ошибка',unknown:'состояние неизвестно'}[job.status]||job.status)+(job.error?' · '+job.error:'')));
    if(model?.status==='missing'&&job.status==='not_started'){
     const start=el('button','Запустить диагностику');const message=el('p','');
     start.onclick=async()=>{
      start.disabled=true;message.textContent='Отправка запроса…';
      try{const response=await fetch('/api/diagnose',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':data.review_token},body:JSON.stringify({incident_id:incident.incident_id})});
       const result=await response.json();if(!response.ok)throw Error(result.error||'Ошибка запуска');message.textContent='Запуск принят';await refresh();
      }catch(error){message.textContent=error.message;start.disabled=false;}
     };box.append(start,message);
    }
   }
   $('incidents').append(box);
  }
 }catch(error){$('error').textContent=error.message;$('status').className='badge stale';$('status').textContent='Нет актуальных данных';}
}
$('refresh').onclick=refresh;refresh();setInterval(refresh,5000);
</script></html>'''


def handler(database, evidence_root, stale_seconds, jobs=None):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path not in ('/api/assessment', '/api/diagnose'):
                self.send_error(404)
                return
            if not secrets.compare_digest(self.headers.get('X-Review-Token', ''), token):
                self.send_error(403)
                return
            status, result = 201, {'saved': True}
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 64000:
                    raise ValueError('Недопустимый размер запроса')
                self.connection.settimeout(5)
                payload = json.loads(self.rfile.read(size))
                identifier = payload['incident_id']
                if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
                    raise ValueError('Некорректный идентификатор инцидента')
                with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as db:
                    if not db.execute('SELECT 1 FROM events WHERE incident_id=? AND event_type=?', (identifier, 'incident_opened')).fetchone():
                        raise ValueError('Инцидент не найден в базе')
                incident = Path(evidence_root) / identifier
                if self.path == '/api/diagnose':
                    if jobs is None:
                        raise ValueError('Запуск модели не включён')
                    jobs.start(identifier)
                    status, result = 202, {'started': True}
                else:
                    if load_model_review(incident, identifier)['status'] != 'available':
                        raise ValueError('Ответ модели или материалы не прошли проверку')
                    if not all(isinstance(payload.get(k), str) for k in ('reviewer', 'verdict', 'note')):
                        raise ValueError('Заполните поля оценки')
                    save_assessment(incident / 'model-review-001', payload['reviewer'], payload['verdict'], payload['note'])
            except FileExistsError:
                status, result = 409, {'error': 'Оценка уже сохранена. Перезапись запрещена.'}
            except ValueError as exc:
                status, result = 400, {'error': str(exc)}
            except (KeyError, TypeError):
                status, result = 400, {'error': 'Проверьте поля оценки и доступность материалов инцидента.'}
            except (OSError, sqlite3.Error):
                status, result = 503, {'error': 'Не удалось сохранить оценку.'}
            body = json.dumps(result, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/':
                body, content_type, status = PAGE.encode('utf-8'), 'text/html; charset=utf-8', 200
            elif self.path == '/api/status':
                try:
                    data = snapshot(database, evidence_root, stale_seconds)
                    data['review_token'] = token
                    data['model_enabled'] = jobs is not None
                    for item in data['incidents']:
                        identifier = item['incident_id']
                        if len(identifier) == 32 and all(c in '0123456789abcdef' for c in identifier):
                            item['model_job'] = job_state(Path(evidence_root) / identifier)
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
    parser.add_argument('--model-python', type=Path, help='Enable model jobs using this virtualenv Python')
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('База мониторинга не найдена')
    if args.stale_seconds <= 0 or not 1 <= args.port <= 65535:
        parser.error('Некорректный порт или порог устаревания')
    if args.model_python and not args.model_python.is_file():
        parser.error('Интерпретатор окружения модели не найден')
    jobs = Jobs(args.evidence_dir, args.model_python) if args.model_python else None
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(args.database, args.evidence_dir, args.stale_seconds, jobs))
    print(f'Откройте http://localhost:{args.port} · Ctrl+C для остановки', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
