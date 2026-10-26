/* Browser-side shrink before upload: big phone photos (often 20 to 50 MB as PNG or 48 MP JPEG) would be refused by the
 * server's 15 MB limit and are shrunk there to 1600 px anyway, so they are shrunk here first and upload quickly. */

/** The server's upload limit (MAX_BYTES in app/server.py). */
export const SERVER_MAX = 15 * 1024 * 1024;
/** Same long side the server shrinks uploads to (UPLOAD_LONG_SIDE). */
const LONG_SIDE = 1600;

const MB = (n: number) => `${Math.round(n / 1024 / 1024)} MB`;
const isHeic = (f: File) => /image\/hei[cf]/.test(f.type) || /\.hei[cf]$/i.test(f.name);
const decodable = (f: File) => /image\/(jpeg|png|webp)/.test(f.type) || /\.(jpe?g|png|webp)$/i.test(f.name);

/** The file to upload: as is when small enough, else re-encoded as JPEG (quality 0.9, at most 1600 px long side,
 * upright from its EXIF rotation), keeping the file name. Throws an Error with a plain message when it cannot be sent. */
export async function shrinkForUpload(file: File): Promise<File> {
  if (isHeic(file) || !decodable(file)) {
    // Chrome cannot decode HEIC; the server can (pillow-heif), so a small one goes as is.
    if (file.size <= SERVER_MAX) return file;
    throw new Error(isHeic(file)
      ? `This HEIC photo is ${MB(file.size)}, over the ${MB(SERVER_MAX)} limit, and the browser cannot shrink HEIC. Export it as JPEG first, or set the iPhone camera to Most Compatible.`
      : `This file is ${MB(file.size)}, over the ${MB(SERVER_MAX)} limit.`);
  }
  let bmp: ImageBitmap;
  try {
    bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    if (file.size <= SERVER_MAX) return file;  // the server says what is wrong with it
    throw new Error(`This image could not be read, and at ${MB(file.size)} it is over the ${MB(SERVER_MAX)} limit.`);
  }
  try {
    const long = Math.max(bmp.width, bmp.height);
    if (file.size <= SERVER_MAX && long <= LONG_SIDE) return file;
    const f = Math.min(1, LONG_SIDE / long);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * f);
    canvas.height = Math.round(bmp.height * f);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("The browser could not shrink this image.");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((ok) => canvas.toBlob(ok, "image/jpeg", 0.9));
    if (!blob) throw new Error("The browser could not shrink this image.");
    return new File([blob], file.name, { type: "image/jpeg", lastModified: file.lastModified });
  } finally {
    bmp.close();
  }
}
