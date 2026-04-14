"""Resume templates and rendering."""

from typing import Dict
from abc import ABC, abstractmethod


class Template(ABC):
    @abstractmethod
    def render_txt(self, data: Dict) -> str:
        pass


class ModernTemplate(Template):
    def render_txt(self, data: Dict) -> str:
        lines = []
        lines.append(data.get("name", "").upper())
        contact = " | ".join(filter(None, [data.get("email", ""), data.get("phone", ""), data.get("linkedin", "")]))
        lines.append(contact)
        lines.append("")

        section_order = data.get("section_order", ["SUMMARY", "SKILLS", "EXPERIENCE", "EDUCATION"])

        for section in section_order:
            if section == "SUMMARY" and "summary" in data:
                lines.extend(["PROFESSIONAL SUMMARY", "=" * 50, data["summary"], ""])
            elif section == "SKILLS" and "skills" in data:
                lines.extend(["TECHNICAL SKILLS", "=" * 50])
                for cat, skills in data["skills"].items():
                    lines.append(f"{cat}: {', '.join(skills)}")
                lines.append("")
            elif section == "EXPERIENCE" and "experience" in data:
                lines.extend(["EXPERIENCE", "=" * 50])
                for role in data["experience"]:
                    lines.append(f"{role.get('title')} | {role.get('company')} | {role.get('dates')}")
                    for bullet in role.get("bullets", []):
                        lines.append(f"  • {bullet}")
                    lines.append("")

        return "\n".join(lines)


class TemplateEngine:
    TEMPLATES = {"modern": ModernTemplate()}

    def render(self, data: Dict, template_name: str = "modern") -> str:
        template = self.TEMPLATES.get(template_name, ModernTemplate())
        return template.render_txt(data)
