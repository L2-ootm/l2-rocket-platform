#!/usr/bin/env python3
"""Generate the verified OSIFOG Level 3 physical-baseline brief."""

from __future__ import annotations

import os
from fpdf import FPDF


NAVY = (7, 17, 31)
PANEL = (18, 34, 59)
TEAL = (39, 226, 177)
AMBER = (255, 190, 63)
WHITE = (242, 247, 252)
MUTED = (145, 164, 188)
INK = (22, 34, 49)
LIGHT = (235, 241, 247)


def safe(value: object) -> str:
    return str(value).encode("latin-1", "replace").decode("latin-1")


class Brief(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_fill_color(*NAVY)
        self.rect(0, 0, self.w, 15, "F")
        self.set_xy(14, 4)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*TEAL)
        self.cell(80, 6, "L2 SYSTEMS / OSIFOG L3")
        self.set_text_color(*MUTED)
        self.cell(0, 6, "PHYSICAL BASELINE / OPENROCKET 24.12", align="R")

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-11)
        self.set_draw_color(205, 216, 227)
        self.line(14, self.get_y(), self.w - 14, self.get_y())
        self.set_y(-9)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(110, 126, 145)
        self.cell(0, 5, f"Verified 2026-07-19     {self.page_no()} / {{nb}}", align="R")

    def start(self, eyebrow: str, title: str, deck: str):
        self.add_page()
        self.set_xy(14, 23)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(28, 155, 127)
        self.cell(0, 5, safe(eyebrow.upper()), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)
        self.set_font("Helvetica", "B", 23)
        self.set_text_color(*INK)
        self.multi_cell(0, 10, safe(title), new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 10)
        self.set_text_color(88, 105, 124)
        self.multi_cell(0, 5, safe(deck), new_x="LMARGIN", new_y="NEXT")
        self.ln(5)

    def section(self, title: str):
        self.ln(2)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*INK)
        self.cell(0, 7, safe(title), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*TEAL)
        self.set_line_width(0.6)
        self.line(self.l_margin, self.get_y(), self.l_margin + 26, self.get_y())
        self.ln(3)

    def paragraph(self, value: str, size: float = 9.1):
        self.set_font("Helvetica", "", size)
        self.set_text_color(*INK)
        self.multi_cell(0, 4.8, safe(value), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def bullets(self, values, check=True):
        for value in values:
            self.set_x(17)
            self.set_font("Helvetica", "B", 8.6)
            self.set_text_color(*(TEAL if check else AMBER))
            self.cell(7, 4.8, "OK" if check else "-")
            self.set_font("Helvetica", "", 8.6)
            self.set_text_color(*INK)
            self.multi_cell(self.w - 42, 4.8, safe(value))
        self.ln(2)

    def callout(self, label: str, value: str, accent=TEAL):
        y = self.get_y()
        self.set_fill_color(*LIGHT)
        self.rect(14, y, self.w - 28, 25, "F")
        self.set_draw_color(*accent)
        self.set_line_width(1.1)
        self.line(14, y, 14, y + 25)
        self.set_xy(20, y + 4)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*accent)
        self.cell(0, 4, safe(label.upper()), new_x="LMARGIN", new_y="NEXT")
        self.set_x(20)
        self.set_font("Helvetica", "", 8.8)
        self.set_text_color(*INK)
        self.multi_cell(self.w - 40, 4.4, safe(value))
        self.set_y(y + 29)

    def metrics(self, values):
        x0, y0 = 14, self.get_y()
        gap = 4
        width = (self.w - 28 - gap) / 2
        for index, (label, value, note) in enumerate(values):
            x = x0 + (index % 2) * (width + gap)
            y = y0 + (index // 2) * 27
            self.set_fill_color(*PANEL)
            self.rect(x, y, width, 23, "F")
            self.set_xy(x + 5, y + 3.5)
            self.set_font("Helvetica", "", 7)
            self.set_text_color(*MUTED)
            self.cell(width - 10, 4, safe(label.upper()), new_x="LEFT", new_y="NEXT")
            self.set_x(x + 5)
            self.set_font("Helvetica", "B", 13.5)
            self.set_text_color(*TEAL)
            self.cell(width - 10, 7, safe(value), new_x="LEFT", new_y="NEXT")
            self.set_x(x + 5)
            self.set_font("Helvetica", "", 6.5)
            self.set_text_color(191, 204, 219)
            self.cell(width - 10, 3.5, safe(note))
        self.set_y(y0 + ((len(values) + 1) // 2) * 27 + 3)

    def table(self, headers, rows, widths):
        self.set_font("Helvetica", "B", 7.2)
        self.set_fill_color(*PANEL)
        self.set_text_color(*WHITE)
        for header, width in zip(headers, widths):
            self.cell(width, 7, safe(header), fill=True)
        self.ln()
        self.set_font("Helvetica", "", 7.2)
        for index, row in enumerate(rows):
            self.set_fill_color(*(LIGHT if index % 2 == 0 else WHITE))
            self.set_text_color(*INK)
            for value, width in zip(row, widths):
                self.cell(width, 6.7, safe(value), fill=True)
            self.ln()
        self.ln(4)


pdf = Brief("P", "mm", "A4")
pdf.alias_nb_pages()
pdf.set_margins(14, 18, 14)
pdf.set_auto_page_break(True, 16)

# Cover
pdf.add_page()
pdf.set_fill_color(*NAVY)
pdf.rect(0, 0, pdf.w, pdf.h, "F")
pdf.set_fill_color(*TEAL)
pdf.rect(0, 0, 7, pdf.h, "F")
pdf.set_xy(22, 28)
pdf.set_font("Helvetica", "B", 9)
pdf.set_text_color(*TEAL)
pdf.cell(0, 6, "OSIFOG 2026 / NIVEL 3 / PROJETO FALCON")
pdf.set_xy(22, 55)
pdf.set_font("Helvetica", "B", 30)
pdf.set_text_color(*WHITE)
pdf.multi_cell(166, 13, "BASELINE FISICO\nVERIFICADO")
pdf.set_xy(22, 90)
pdf.set_font("Helvetica", "", 11)
pdf.set_text_color(*MUTED)
pdf.multi_cell(158, 6, "Estrutura real. Vento oficial. Dois pousos ativos.\nPonto seguro para a proxima sessao de otimizacao.")
pdf.set_xy(22, 132)
pdf.set_font("Helvetica", "", 8)
pdf.cell(0, 5, "PONTUACAO SALVA E REABERTA")
pdf.set_xy(22, 142)
pdf.set_font("Helvetica", "B", 33)
pdf.set_text_color(*TEAL)
pdf.cell(0, 14, "839.696,05")
pdf.set_xy(22, 166)
pdf.set_fill_color(*PANEL)
pdf.rect(22, 166, 165, 57, "F")
cover = [
    ("APOGEU", "3000,031 m"), ("MACH MAX", "0,943"),
    ("MARGEM MIN", "1,502 cal"), ("POUSOS", "2,648 / 2,459 m/s"),
]
for index, (label, value) in enumerate(cover):
    x = 31 + (index % 2) * 79
    y = 176 + (index // 2) * 23
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*MUTED)
    pdf.cell(70, 4, label)
    pdf.set_xy(x, y + 5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*WHITE)
    pdf.cell(70, 6, value)
pdf.set_xy(22, 248)
pdf.set_font("Helvetica", "", 8)
pdf.set_text_color(*MUTED)
pdf.multi_cell(165, 5, "AUTHORITY: osifog_physical_839k_falcon.ork\nSHA-256: 7118214A6DFF2B06...FB86EB38")

# Authority
pdf.start("01 / authority", "Resultado verificado, sem promessa de 850k", "Os valores foram reabertos da simulacao armazenada no arquivo final. O artefato antigo com nome 850k permanece em quarentena por colisao interna.")
pdf.metrics([
    ("Pontuacao", "839.696,05", "formula oficial completa"),
    ("Apogeu", "3000,031 m", "erro: +0,031 m"),
    ("Sustainer", "2,648 m/s", "E +58,164 / N +109,440 m"),
    ("Booster", "2,459 m/s", "E +70,282 / N +52,926 m"),
    ("Simulacao", "1 salva", "2 branches aterrissados"),
    ("Violacoes", "zero", "anti-tumble presente"),
])
pdf.callout("Estado correto", "Este e um baseline legal e fisicamente auditavel para continuar a otimizacao. 850k ainda nao foi demonstrado por um arquivo salvo e reaberto.", AMBER)

# Physical architecture
pdf.start("02 / physical design", "Nada atravessa motores", "A engine agora compila os componentes internos como solidos finitos antes de chamar o OpenRocket.")
pdf.table(["Elemento", "Implementacao nativa", "Funcao"], [
    ["Cluster 3+1", "InnerTube, folga 0,25 mm", "subida + retro"],
    ["Suportes", "3 CenteringRing em fibra", "caminho de carga"],
    ["Interstage", "TubeCoupler interno 50 mm", "25 mm por lado"],
    ["Lastro booster", "3 barras de aco R14 mm", "coladas ao tubo central"],
    ["Lastro nariz", "Bulkhead de aco 1,260 kg", "CG e altitude"],
], [42, 82, 58])
pdf.bullets([
    "Diametro maximo 148 mm; comprimento total 2,190 m.",
    "Tubos principais tangentes ao tubo central; nenhuma sobreposicao.",
    "Anel possui abertura de 137,5 mm e nao bloqueia nenhum motor.",
    "Lastros possuem densidade, volume e massa geometricamente consistentes.",
    "Sem paraquedas, streamer, massa magica, override de CG ou Cd.",
])
pdf.callout("Quarentena", "osifog_850k_falcon.ork nao e seguro: o antigo disco de lastro traseiro intersecta os quatro mounts do booster.", AMBER)

# Rules and environment
pdf.start("03 / rules and environment", "Gates de desclassificacao preservados", "A correcao estrutural nao alterou o cenario oficial da prova.")
pdf.bullets([
    "Latitude 28,5621; longitude -80,5772; altitude de lancamento 3 m.",
    "Temperatura 30,1 C e pressao 1000 hPa.",
    "OpenWind_File.csv com 28 niveis e referencia AGL.",
    "Direcoes entram em graus no CSV e sao persistidas em radianos no .ork.",
    "Seed 16000, guia de 6 m, azimute 34 graus e inclinacao 3,85 graus.",
    "Mach 0,943 < 0,95; margem inicial de subida 1,502 >= 1,5 cal.",
    "Ambos os estagios pousam abaixo de 5 m/s e sem recuperacao passiva.",
])
pdf.callout("Correcao de avaliacao", "A margem de subida agora termina no primeiro apogeu. Uma subida curta causada pelo retro perto do solo nao e mais confundida com a ascensao inicial.")

# Sequence and score
pdf.start("04 / sequence and score", "A ilha legal e estreita", "Milissegundos mudam o retro entre frenagem util, impacto e relancamento.")
pdf.table(["Evento", "Atraso", "Resultado"], [
    ["Separacao booster", "burnout + 36,500 s", "dois branches"],
    ["Retro sustainer", "launch + 54,309682 s", "2,648 m/s"],
    ["Retro booster", "launch + 65,280526 s", "2,459 m/s"],
], [58, 58, 66])
pdf.section("Descontos da formula")
pdf.table(["Termo", "Desconto"], [
    ["Erro de altitude", "2,883"],
    ["Posicao no apogeu", "172,880"],
    ["Posicao media dos pousos", "21.430,546"],
    ["Velocidade media", "3.260,136"],
    ["Propelente consumido", "35.437,500"],
    ["RESULTADO", "839.696,05"],
], [112, 70])
pdf.callout("Gargalo atual", "A altitude ja esta resolvida. O maior ganho possivel agora vem de reduzir o deslocamento medio dos pousos sem sair da ilha legal de delays.", AMBER)

# Engine and next session
pdf.start("05 / engine and next session", "O que foi corrigido e onde continuar", "A proxima sessao pode iniciar diretamente no polishing de trajetoria.")
pdf.bullets([
    "Collision gate interno, containment e validacao massa-volume.",
    "Busca rejeita motor programado para acender depois do impacto.",
    "Separacao so e ranqueada depois de calibrar os dois estagios.",
    "Score e espaco de busca vem do manifesto JSON, sem tabela duplicada.",
    "Rust calcula trajetoria horizontal, pouso por estagio e score dinamico.",
    "OpenRocket continua autoridade para motores main + retro independentes.",
])
pdf.section("Experimento interrompido")
pdf.paragraph("Retomar o sweep local de delays transportados para os bulkheads exatos: 1,270 kg @ 0,470 m; 1,280 kg @ 0,500 m; 1,280 kg @ 0,450/0,460/0,470 m. Salvar e reabrir cada finalista antes de comparar.")
pdf.callout("Meta", "Buscar 850k, mas documentar somente scores comprovados no arquivo salvo. O baseline seguro desta sessao e 839.696,05.", AMBER)

# Package
pdf.start("06 / package", "Arquivos para a retomada", "O pacote separa claramente o artefato valido do antigo arquivo em quarentena.")
pdf.bullets([
    "designs/osifog_level3/osifog_physical_839k_falcon.ork",
    "designs/osifog_level3/osifog_physical_839k_falcon.json",
    "designs/osifog_level3/openearth/physical_839k/ - CSVs dos dois branches",
    "handoff.md - historia, bugs, parametros e proximo experimento",
    ".planning/.continue-here.md e .planning/HANDOFF.json",
    ".planning/ultra/simulation/SIMULATION-MASTER-REPORT.md",
])
pdf.section("Integridade")
pdf.paragraph("SHA-256 completo do ORK: 7118214A6DFF2B06C164B02D0574786E133601B1502CED9F24532F20FB86EB38")
pdf.callout("Antes de enviar", "Ainda falta produzir nova evidencia OpenEarth/Google Earth para este artefato 839k. As imagens antigas nao devem ser apresentadas como se correspondessem ao novo arquivo.", AMBER)

output_path = os.path.join("OSIFOG", "OSIFOG_Level3_Brief.pdf")
pdf.output(output_path)
print(f"PDF generated: {output_path}")
