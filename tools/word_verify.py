"""Open a built report in real Microsoft Word, update fields, and report the results.

Usage: word_verify.py report.docx output_dir
Writes output_dir/report.pdf and page-NN.png, and prints a summary. Developer tool only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pypdfium2
import win32com.client


def main() -> None:
    source = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        document = word.Documents.Open(str(source), ReadOnly=False, AddToRecentFiles=False, OpenAndRepair=False)
        document.Fields.Update()
        for toc in document.TablesOfContents:
            toc.Update()
        for tof in document.TablesOfFigures:
            tof.Update()
        print("pages:", document.ComputeStatistics(2))
        print("sections:", document.Sections.Count)
        print("tocs:", document.TablesOfContents.Count, "lists:", document.TablesOfFigures.Count)
        print("tables:", document.Tables.Count, "inline images:", document.InlineShapes.Count, "shapes:", document.Shapes.Count)
        if document.TablesOfContents.Count:
            toc_text = document.TablesOfContents(1).Range.Text
            print("toc lines:", toc_text.count("\r"))
            print("toc start:", toc_text[:260].replace("\r", " | "))
        for index in range(1, document.TablesOfFigures.Count + 1):
            text = document.TablesOfFigures(index).Range.Text
            print(f"list {index} lines:", text.count("\r"), "|", text[:170].replace("\r", " | "))
        content = document.Content.Text
        print("field errors:", content.count("Error!"))
        document.ExportAsFixedFormat(str(out / "report.pdf"), 17)
        document.Close(0)
    finally:
        word.Quit()
    pdf = pypdfium2.PdfDocument(str(out / "report.pdf"))
    print("pdf pages:", len(pdf))
    for index in range(len(pdf)):
        pdf[index].render(scale=0.9).to_pil().save(out / f"page-{index + 1:02d}.png")
    pdf.close()


if __name__ == "__main__":
    main()
