from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from vacancy_agent.approval_adapters.base import ApprovalRequest


class EditorOpenError(RuntimeError):
    pass


class CoverLetterEditor:
    marker = "----- РЕДАКТИРУЙ СОПРОВОДИТЕЛЬНОЕ ПИСЬМО НИЖЕ -----"

    def edit(self, request: ApprovalRequest) -> str:
        with tempfile.TemporaryDirectory(prefix="vacancy-agent-letter-") as temp_dir:
            file_path = self._create_edit_file(Path(temp_dir), request)
            self._open_editor(file_path)
            return self._read_edited_text(file_path)

    def _create_edit_file(self, temp_dir: Path, request: ApprovalRequest) -> Path:
        vacancy = request.vacancy
        file_path = temp_dir / f"cover_letter_{vacancy.id}.md"

        content = "\n".join(
            [
                "# Редактирование сопроводительного письма",
                "",
                f"Вакансия: {vacancy.title}",
                f"Компания: {vacancy.company}",
                f"URL: {vacancy.url}",
                "",
                "Инструкция:",
                "1. Редактируй только текст ниже разделителя.",
                "2. Сохрани файл.",
                "3. Закрой окно редактора или вкладку файла.",
                "4. Агент заберёт текст и продолжит flow.",
                "",
                self.marker,
                "",
                request.draft_text.strip(),
                "",
            ]
        )

        file_path.write_text(content, encoding="utf-8")
        return file_path

    def _open_editor(self, file_path: Path) -> None:
        command = self._resolve_editor_command()
        args = [*shlex.split(command), str(file_path)]

        completed = subprocess.run(args, check=False)

        if completed.returncode != 0:
            raise EditorOpenError(
                f"Редактор завершился с кодом {completed.returncode}: {' '.join(args)}"
            )

    def _resolve_editor_command(self) -> str:
        custom_editor = os.getenv("VACANCY_AGENT_EDITOR") or os.getenv("EDITOR")

        if custom_editor:
            return custom_editor

        if shutil.which("code"):
            return "code --wait"

        if platform.system() == "Darwin":
            return "open -W -a TextEdit"

        if shutil.which("nano"):
            return "nano"

        if shutil.which("vim"):
            return "vim"

        raise EditorOpenError(
            "Не найден редактор. Укажи VACANCY_AGENT_EDITOR, например: "
            'export VACANCY_AGENT_EDITOR="code --wait"'
        )

    def _read_edited_text(self, file_path: Path) -> str:
        content = file_path.read_text(encoding="utf-8")

        if self.marker in content:
            content = content.split(self.marker, 1)[1]

        return content.strip()