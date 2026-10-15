# Публикация / Publishing

## Русский

Проект подготовлен как самостоятельный репозиторий. Создайте на GitHub пустой репозиторий `bfc50-tools`, без автоматического README и LICENSE. В командах замените `YOUR_LOGIN` своим логином.

Из корня этого проекта, после установки по README:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
git status
git diff --check
```

Если Git ещё не инициализирован:

```bash
git init -b main
git add .gitignore pyproject.toml LICENSE NOTICE.md README.md README.ru.md
git add bfc50_tools profiles tests docs .github
git diff --cached --stat
git diff --cached --check
git commit -m "Add BF-C50 configuration tools and bilingual documentation"
```

Если файлы уже сохранены первоначальным commit, повторять `git init` и commit не нужно. Git использует имя и email, настроенные вами. Первоначальный commit можно пометить тегом после проверки:

```bash
git tag -a v0.1.0 -m "Initial experimental release"
git remote add origin https://github.com/YOUR_LOGIN/bfc50-tools.git
git push -u origin main
git push origin v0.1.0
```

После push проверьте вкладку Actions. Тесты в CI не подключаются к радио и не подтверждают аппаратную совместимость. Упакованная команда записи уже прошла описанную в `VALIDATION.md` минимальную аппаратную проверку с возвратом исходной памяти; для последующих изменений такие проверки нужно повторять. После прохождения CI можно оформить экспериментальный релиз, указав точные границы проверенного.

Для будущих изменений:

```bash
git switch -c feature/describe-the-change
```

В репозиторий входят код, документация, базовый CSV и синтетические тесты. Рабочие `backups/`, `logs/`, `plans/`, виртуальное окружение и сборочные файлы исключены через `.gitignore`. Новые пользовательские данные добавляйте только осознанно.

## English

Create an empty `bfc50-tools` repository in your GitHub account, without generated README/license files. Replace `YOUR_LOGIN` in the commands above. If the checkout already has an initial commit, retain it; otherwise initialize `main`, stage the listed publication files and commit. Review the staged diff before pushing.

Push `main` and an optional `v0.1.0` tag, then inspect the Actions results. CI exercises synthetic radios only. An experimental release should state which CLI hardware operations were actually exercised, distinguishing earlier-script radio tests from this refactored package's write acceptance test.

Local backups, logs, plans, environments and build products are ignored. The default profile is the first eight analogue PMR446 frequencies with both CTCSS directions OFF; channel configuration alone does not establish regulatory compliance.
