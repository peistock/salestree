declare module "pdf2json" {
  interface Pdf2JsonError {
    parserError: Error;
  }

  interface Pdf2JsonData {
    Pages: Array<{
      Texts: Array<{
        R: Array<{ T: string }>;
      }>;
    }>;
  }

  class PDFParser {
    loadPDF(filePath: string): void;
    on(event: "pdfParser_dataReady", handler: (data: Pdf2JsonData) => void): void;
    on(event: "pdfParser_dataError", handler: (err: Pdf2JsonError) => void): void;
  }

  export = PDFParser;
}
