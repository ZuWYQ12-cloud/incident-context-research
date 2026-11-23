#!/usr/bin/env bash
# Local development permission-failure capture; operator knows the intervention.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
BASE=stand/compose.yaml
docker info >/dev/null
sql() {
  timeout --foreground --kill-after=5s 20s docker compose -f "$BASE" exec -T --interactive=false db \
    psql -X -w -U stand_admin -d production -v ON_ERROR_STOP=1 -At -c "$1" </dev/null
}
# Require a direct, non-grantable privilege so GRANT restores that privilege.
direct=$(sql "SELECT EXISTS (SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE c.oid='public.production_tasks'::regclass AND a.grantee='eval_auth'::regrole AND a.grantor='stand_admin'::regrole AND a.privilege_type='SELECT' AND NOT a.is_grantable);")
[[ "$direct" == t ]] || { echo 'Нет ожидаемого прямого SELECT от stand_admin. Права не изменялись.'; exit 1; }
RUN_DIR="results/raw/$(date -u +%Y%m%dT%H%M%S)-C03-monitor-$$"
mkdir -p "$RUN_DIR/observations" "$RUN_DIR/operator"
cp "$0" "$RUN_DIR/operator/executed-script.sh"
git rev-parse HEAD > "$RUN_DIR/operator/code_commit.txt"
now() { date -u +%Y-%m-%dT%H:%M:%S.%NZ; }
START=$(now)
printf '%s\n' "$START" > "$RUN_DIR/operator/start_utc.txt"
echo "Материалы: $RUN_DIR"
capture() {
  local phase=$1 endpoint prefix code
  for endpoint in health/live health/ready tasks; do
    prefix="$RUN_DIR/observations/${phase}_${endpoint//\//_}"
    now > "$prefix.started_at.txt"
    code=0
    curl -sS --max-time 10 -D "$prefix.headers" -o "$prefix.body" \
      -w 'http_code=%{http_code}\nduration_seconds=%{time_total}\n' \
      "http://127.0.0.1:8000/$endpoint" > "$prefix.meta" 2> "$prefix.stderr" || code=$?
    printf 'curl_exit_code=%s\n' "$code" >> "$prefix.meta"
    now > "$prefix.collected_at.txt"
  done
}
check() {
  local phase=$1 live=$2 ready=$3 tasks=$4 name expected
  for name in health_live health_ready tasks; do
    case "$name" in health_live) expected=$live;; health_ready) expected=$ready;; tasks) expected=$tasks;; esac
    grep -qx "http_code=$expected" "$RUN_DIR/observations/${phase}_$name.meta" || return 1
    grep -qx 'curl_exit_code=0' "$RUN_DIR/observations/${phase}_$name.meta" || return 1
  done
}
restore_needed=0
finish() {
  local original=$? code=0
  trap - EXIT INT TERM
  set +e
  if [[ "$restore_needed" == 1 ]]; then
    echo 'Восстанавливаем право SELECT...'
    now > "$RUN_DIR/operator/restore_started_at.txt"
    sql 'GRANT SELECT ON TABLE public.production_tasks TO eval_auth;' > "$RUN_DIR/operator/restore.log" 2>&1
    code=$?
    printf '%s\n' "$code" > "$RUN_DIR/operator/restore_exit_code.txt"
    capture after
    if check after 200 200 200; then
      echo "Восстановление подтверждено: три HTTP 200. Код SQL-команды: $code"
    else
      echo 'ВОССТАНОВЛЕНИЕ НЕ ПОДТВЕРЖДЕНО. Проверьте operator/restore.log.'
      original=1
    fi
    [[ "$code" == 0 ]] || original=1
    now > "$RUN_DIR/operator/restore_checked_at.txt"
  fi
  printf '%s\n' "$original" > "$RUN_DIR/operator/script_exit_code.txt"
  for file in "$RUN_DIR"/observations/*.meta; do
    [[ -f "$file" ]] || continue
    printf '\n%s\n' "${file##*/}"
    cat "$file"
  done
  echo "Материалы сохранены: $RUN_DIR"
  exit "$original"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
capture before
check before 200 200 200 || { echo 'Исходное состояние не подтверждено. Права не менялись.'; exit 1; }
restore_needed=1
now > "$RUN_DIR/operator/injection_started_at.txt"
code=0
sql 'REVOKE SELECT ON TABLE public.production_tasks FROM eval_auth;' > "$RUN_DIR/operator/injection.log" 2>&1 || code=$?
printf '%s\n' "$code" > "$RUN_DIR/operator/injection_exit_code.txt"
[[ "$code" == 0 ]] || { echo 'Ошибка команды отзыва. Переходим к восстановлению.'; exit 1; }
can_read=$(sql "SELECT has_table_privilege('eval_auth','public.production_tasks','SELECT');")
printf '%s\n' "$can_read" > "$RUN_DIR/operator/can_read_after_revoke.txt"
[[ "$can_read" == f ]] || { echo 'Чтение всё ещё разрешено, возможно через другие роли. Восстанавливаем SELECT.'; exit 1; }
capture during
check during 200 503 500 || { echo 'Не получены ожидаемые 200/503/500. Восстанавливаем SELECT.'; exit 1; }
echo 'Сбой воспроизведён: live=200, ready=503, tasks=500. Ожидаем монитор 45 секунд.'
sleep 45
for service in api db; do
  code=0
  timeout --kill-after=5s 20s docker compose -f "$BASE" logs --no-color --timestamps --since "$START" "$service" \
    > "$RUN_DIR/observations/${service}_before_recovery.log" 2> "$RUN_DIR/operator/${service}_logs.stderr" || code=$?
  printf '%s\n' "$code" > "$RUN_DIR/operator/${service}_logs_exit_code.txt"
done
now > "$RUN_DIR/operator/diagnosis_cutoff_utc.txt"
echo 'Сбор завершён. Переходим к восстановлению SELECT.'
