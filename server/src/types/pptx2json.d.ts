declare module "pptx2json" {
  class PPTX2Json {
    toJson(file: string): Promise<unknown>;
    buffer2json(buffer: Buffer): Promise<unknown>;
  }

  export = PPTX2Json;
}
