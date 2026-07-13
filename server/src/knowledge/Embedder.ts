import { env, pipeline } from "@xenova/transformers";

env.allowRemoteModels = true;
env.allowLocalModels = true;
env.remoteHost = "https://hf-mirror.com/";
env.localModelPath = "/Users/cpp/.cache/xiaoxiaoshu/models";

const MODEL_NAME = "Xenova/bge-small-zh-v1.5";

export class Embedder {
  private static instance: Embedder;
  private pipe: any = null;
  private loading: Promise<any> | null = null;

  static getInstance(): Embedder {
    if (!Embedder.instance) {
      Embedder.instance = new Embedder();
    }
    return Embedder.instance;
  }

  async embed(text: string): Promise<number[]> {
    if (!this.pipe) {
      if (!this.loading) {
        this.loading = pipeline("feature-extraction", MODEL_NAME);
      }
      this.pipe = await this.loading;
      this.loading = null;
    }

    const output = await this.pipe(text, {
      pooling: "mean",
      normalize: true,
    });

    return Array.from(output.data as Float32Array);
  }
}
