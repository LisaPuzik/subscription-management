#!/usr/bin/env bash
set -u
API=${API_URL:-http://localhost:8000/api/v1}
JAR=$(mktemp)
LOG=${SERVER_LOG:?set SERVER_LOG to the uvicorn output file}
EMAIL=smoke.user@example.com
PASS=SmokeTestPass2026

json() { python -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }

call() {
  local name=$1 expected=$2; shift 2
  local out code
  out=$(curl -s -w '\n%{http_code}' -b "$JAR" -c "$JAR" "$@")
  code=${out##*$'\n'}
  BODY=${out%$'\n'*}
  local mark=OK; [ "$code" = "$expected" ] || mark=FAIL
  printf '%-4s | %-58s | %s (ожид. %s)\n' "$mark" "$name" "$code" "$expected"
}

token_from_log() { grep -o "$1?token=[A-Za-z0-9_-]*" "$LOG" | tail -1 | cut -d= -f2; }

call "POST /auth/register" 201 -X POST $API/auth/register -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}"
call "POST /auth/register (повтор email)" 409 -X POST $API/auth/register -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}"
call "POST /auth/jwt/login (email не подтвержден)" 403 -X POST $API/auth/jwt/login -d "username=$EMAIL&password=$PASS"
sleep 1
VT=$(token_from_log verify-email)
call "POST /auth/verify" 200 -X POST $API/auth/verify -H 'Content-Type: application/json' -d "{\"token\":\"$VT\"}"
call "POST /auth/jwt/login (неверный пароль)" 400 -X POST $API/auth/jwt/login -d "username=$EMAIL&password=wrong-password"
call "POST /auth/jwt/login" 200 -X POST $API/auth/jwt/login -d "username=$EMAIL&password=$PASS"
TOKEN=$(echo "$BODY" | json 'd["access_token"]')
AUTH="Authorization: Bearer $TOKEN"
call "GET /users/me без токена" 401 $API/users/me
call "PATCH /users/me (timezone)" 200 -X PATCH $API/users/me -H "$AUTH" -H 'Content-Type: application/json' -d '{"timezone":"Asia/Krasnoyarsk"}'
CSRF=$(grep csrf_token "$JAR" | awk '{print $7}')
call "POST /auth/refresh (ротация токена)" 200 -X POST $API/auth/refresh -H "X-CSRF-Token: $CSRF"
TOKEN=$(echo "$BODY" | json 'd["access_token"]')
AUTH="Authorization: Bearer $TOKEN"

call "POST /categories" 201 -X POST $API/categories -H "$AUTH" -H 'Content-Type: application/json' -d '{"name":"Streaming","color":"#3498db"}'
CAT=$(echo "$BODY" | json 'd["id"]')
call "GET /categories" 200 "$API/categories?limit=100" -H "$AUTH"

call "POST /subscriptions без Idempotency-Key" 422 -X POST $API/subscriptions -H "$AUTH" -H 'Content-Type: application/json' -d '{}'
SUB_BODY="{\"service_name\":\"Netflix\",\"amount\":\"15.99\",\"currency\":\"EUR\",\"billing_interval_unit\":\"month\",\"interval_count\":1,\"start_date\":\"2026-08-31\",\"category_id\":\"$CAT\"}"
call "POST /subscriptions" 201 -X POST $API/subscriptions -H "$AUTH" -H 'Idempotency-Key: smoke-subscription-0001' -H 'Content-Type: application/json' -d "$SUB_BODY"
SUB=$(echo "$BODY" | json 'd["id"]'); NEXT=$(echo "$BODY" | json 'd["next_payment_date"]')
echo "       next_payment_date = $NEXT (якорь 31.08, ноябрь -> 30-е)"
call "POST /subscriptions (тот же ключ, повтор)" 201 -X POST $API/subscriptions -H "$AUTH" -H 'Idempotency-Key: smoke-subscription-0001' -H 'Content-Type: application/json' -d "$SUB_BODY"
echo "       тот же id: $( [ "$(echo "$BODY" | json 'd["id"]')" = "$SUB" ] && echo да || echo НЕТ )"
call "POST /subscriptions (тот же ключ, другое тело)" 409 -X POST $API/subscriptions -H "$AUTH" -H 'Idempotency-Key: smoke-subscription-0001' -H 'Content-Type: application/json' -d "${SUB_BODY/15.99/1.00}"
call "POST /subscriptions (weekly, Gym)" 201 -X POST $API/subscriptions -H "$AUTH" -H 'Idempotency-Key: smoke-subscription-0002' -H 'Content-Type: application/json' -d '{"service_name":"Gym","amount":"7.00","currency":"EUR","billing_interval_unit":"week","interval_count":1,"start_date":"2026-11-02"}'
call "POST /subscriptions (amount числом)" 422 -X POST $API/subscriptions -H "$AUTH" -H 'Idempotency-Key: smoke-subscription-0003' -H 'Content-Type: application/json' -d '{"service_name":"X","amount":15.99,"currency":"EUR","billing_interval_unit":"month","interval_count":1,"start_date":"2026-11-01"}'
call "GET /subscriptions?sort_by=amount" 200 "$API/subscriptions?sort_by=amount&sort_order=desc" -H "$AUTH"
echo "       total = $(echo "$BODY" | json 'd["pagination"]["total"]')"
call "PATCH /subscriptions/{id} (amount 19.99)" 200 -X PATCH $API/subscriptions/$SUB -H "$AUTH" -H 'Content-Type: application/json' -d '{"amount":"19.99"}'

call "POST /payments (оплата текущего периода)" 201 -X POST $API/payments -H "$AUTH" -H 'Idempotency-Key: smoke-payment-000001' -H 'Content-Type: application/json' -d "{\"subscription_id\":\"$SUB\",\"amount\":\"19.99\",\"currency\":\"EUR\",\"occurred_at\":\"2026-11-10T10:00:00+07:00\",\"billing_period_start\":\"$NEXT\"}"
PAY=$(echo "$BODY" | json 'd["id"]')
call "GET /subscriptions/{id} (сдвиг расписания)" 200 $API/subscriptions/$SUB -H "$AUTH"
echo "       next_payment_date: $NEXT -> $(echo "$BODY" | json 'd["next_payment_date"]')"
call "POST /payments (тот же период)" 409 -X POST $API/payments -H "$AUTH" -H 'Idempotency-Key: smoke-payment-000002' -H 'Content-Type: application/json' -d "{\"subscription_id\":\"$SUB\",\"amount\":\"19.99\",\"currency\":\"EUR\",\"occurred_at\":\"2026-11-10T10:00:00+07:00\",\"billing_period_start\":\"$NEXT\"}"
call "POST /payments (валюта USD != EUR)" 422 -X POST $API/payments -H "$AUTH" -H 'Idempotency-Key: smoke-payment-000003' -H 'Content-Type: application/json' -d "{\"subscription_id\":\"$SUB\",\"amount\":\"19.99\",\"currency\":\"USD\",\"occurred_at\":\"2026-11-10T10:00:00Z\",\"billing_period_start\":\"2026-08-31\"}"
call "POST /payments (разовый расход, USD)" 201 -X POST $API/payments -H "$AUTH" -H 'Idempotency-Key: smoke-payment-000004' -H 'Content-Type: application/json' -d '{"service_name":"Taxi","amount":"12.50","currency":"USD","occurred_at":"2026-11-05T09:00:00Z"}'
call "POST /payments/{id}/refunds (5.00)" 201 -X POST $API/payments/$PAY/refunds -H "$AUTH" -H 'Idempotency-Key: smoke-refund-0000001' -H 'Content-Type: application/json' -d '{"amount":"5.00","occurred_at":"2026-11-10T12:00:00Z"}'
call "POST /payments/{id}/refunds (превышение)" 409 -X POST $API/payments/$PAY/refunds -H "$AUTH" -H 'Idempotency-Key: smoke-refund-0000002' -H 'Content-Type: application/json' -d '{"amount":"15.00","occurred_at":"2026-11-10T12:00:00Z"}'
call "POST /payments/{id}/reverse (есть возврат)" 409 -X POST $API/payments/$PAY/reverse -H "$AUTH" -H 'Content-Type: application/json' -d '{"reason":"Recorded by mistake"}'
call "GET /payments?date_from=2026-11-01" 200 "$API/payments?date_from=2026-11-01&date_to=2026-12-01" -H "$AUTH"
echo "       операций: $(echo "$BODY" | json 'd["pagination"]["total"]')"

call "GET /analytics/summary" 200 "$API/analytics/summary?date_from=2026-11-01&date_to=2026-12-01" -H "$AUTH"
echo "       $(echo "$BODY" | json '[(t["currency"], t["gross_charges"], t["refunds"], t["net_expenses"]) for t in d["totals_by_currency"]]')"
call "GET /analytics/monthly" 200 "$API/analytics/monthly?date_from=2026-09-01&date_to=2026-12-01" -H "$AUTH"
echo "       EUR: $(echo "$BODY" | json '[(m["month"], m["net_expenses"]) for m in d["items"][0]["months"]]')"
call "GET /analytics/categories" 200 "$API/analytics/categories?date_from=2026-11-01&date_to=2026-12-01" -H "$AUTH"
echo "       $(echo "$BODY" | json '[(i["currency"], [(c["category_name"], c["net_expenses"]) for c in i["categories"]]) for i in d["items"]]')"
call "GET /analytics/upcoming?month=2026-12" 200 "$API/analytics/upcoming?month=2026-12" -H "$AUTH"
echo "       $(echo "$BODY" | json '[(t["currency"], t["forecast_amount"]) for t in d["totals_by_currency"]], len(d["items"])')"
call "GET /analytics/summary без date_to" 422 "$API/analytics/summary?date_from=2026-11-01" -H "$AUTH"

call "DELETE /subscriptions/{id} (архив)" 204 -X DELETE $API/subscriptions/$SUB -H "$AUTH"
call "PATCH архивной подписки" 409 -X PATCH $API/subscriptions/$SUB -H "$AUTH" -H 'Content-Type: application/json' -d '{"amount":"1.00"}'
call "GET /payments/{id} (история сохранена)" 200 $API/payments/$PAY -H "$AUTH"
call "POST /subscriptions/{id}/restore" 200 -X POST $API/subscriptions/$SUB/restore -H "$AUTH"

call "POST /auth/register (второй пользователь)" 201 -X POST $API/auth/register -H 'Content-Type: application/json' -d "{\"email\":\"other@example.com\",\"password\":\"$PASS\"}"
sleep 1
VT2=$(grep -o "verify-email?token=[A-Za-z0-9_-]*" "$LOG" | tail -1 | cut -d= -f2)
curl -s -o /dev/null -X POST $API/auth/verify -H 'Content-Type: application/json' -d "{\"token\":\"$VT2\"}"
OTHER=$(curl -s -X POST $API/auth/jwt/login -d "username=other@example.com&password=$PASS" | json 'd["access_token"]')
call "GET чужой подписки" 404 $API/subscriptions/$SUB -H "Authorization: Bearer $OTHER"
call "GET чужого платежа" 404 $API/payments/$PAY -H "Authorization: Bearer $OTHER"

call "POST /auth/forgot-password (существующий)" 202 -X POST $API/auth/forgot-password -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\"}"
call "POST /auth/forgot-password (несуществующий)" 202 -X POST $API/auth/forgot-password -H 'Content-Type: application/json' -d '{"email":"ghost@example.com"}'
CSRF=$(grep csrf_token "$JAR" | awk '{print $7}')
call "POST /auth/jwt/logout" 204 -X POST $API/auth/jwt/logout -H "$AUTH" -H "X-CSRF-Token: $CSRF"
call "DELETE /users/me" 204 -X DELETE $API/users/me -H "$AUTH" -H 'Content-Type: application/json' -d "{\"password\":\"$PASS\",\"confirmation\":\"DELETE\"}"
call "GET /users/me после удаления" 401 $API/users/me -H "$AUTH"
