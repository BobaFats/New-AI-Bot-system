#!/bin/sh
# Проверка подготовленных к коммиту изменений (git add): нет ли в них паролей, токенов и ключей.
#
# Запуск перед коммитом (Git Bash или терминал VS Code с bash):
#     git add -A
#     sh scripts/check_secrets.sh
# Код выхода 0 — всё чисто, 1 — найдено похожее на секрет (коммитить нельзя).
#
# Сами найденные значения на экран не выводятся — только файл и тип находки.
# Чтобы проверка запускалась автоматически при каждом git commit, её можно подключить
# как git-хук (делается один раз, вручную):
#     cp scripts/check_secrets.sh .git/hooks/pre-commit

fail=0
report() { echo "check_secrets: $1"; fail=1; }

# 1. Файлы, которые не должны попадать в репозиторий никогда.
for f in $(git diff --cached --name-only --diff-filter=ACM); do
  case "$f" in
    .env|*/.env|*.db|*.db-journal) report "в коммите секретный файл: $f" ;;
  esac
done

# 2. Значения из .env длиной от 8 символов (кроме несекретных настроек).
env_values=""
if [ -f .env ]; then
  env_values=$(grep -vE '^\s*(#|$)' .env | tr -d '\r' \
    | grep -vE '^(DATABASE_URL|DEEPSEEK_BASE_URL|JIVO_API_BASE|LOG_LEVEL|SEARCH_PROVIDER|WEB_USER_AGENT)=' \
    | sed -n 's/^[A-Za-z_]*=//p' | awk 'length($0) >= 8')
fi

# 3. Проверяем добавляемые строки каждого файла.
for f in $(git diff --cached --name-only --diff-filter=ACM); do
  added=$(git diff --cached -U0 --no-color -- "$f" | grep '^+' | grep -v '^+++')
  [ -z "$added" ] && continue
  printf '%s\n' "$added" | grep -qE '[A-Za-z0-9]{8}:[0-9a-f]{40}' && report "$f: похоже на токен бота Jivo"
  printf '%s\n' "$added" | grep -qE 'sk-[A-Za-z0-9]{20,}' && report "$f: похоже на API-ключ"
  if [ -n "$env_values" ]; then
    printf '%s\n' "$env_values" | while IFS= read -r v; do
      printf '%s\n' "$added" | grep -qF -- "$v" && echo "x"
    done | grep -q x && report "$f: содержит значение из вашего .env"
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "Найдены секреты. Замените значения заглушками вида [ваш токен], [ваш пароль], [ваш ключ]."
  exit 1
fi
echo "check_secrets: секретов не найдено"
exit 0
