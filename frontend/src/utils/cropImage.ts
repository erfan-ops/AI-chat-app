/**
 * Browser-side cropping: turn the user's 1:1 selection into the avatar file that
 * gets uploaded. The original image never leaves the browser.
 */

/** The avatar master resolution — every display size is derived from it. */
export const AVATAR_SIZE = 512

/** A crop rectangle in source-image pixels (react-easy-crop's `croppedAreaPixels`). */
export interface CropArea {
  x: number
  y: number
  width: number
  height: number
}

/** Load an image from an object URL, rejecting if the browser cannot decode it. */
function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('That image could not be read.'))
    image.src = src
  })
}

/**
 * Render the selected crop to a 512×512 blob.
 *
 * `area` comes from react-easy-crop's `croppedAreaPixels`, which is already
 * expressed in source-image pixels — drawing it with a 1:1 source/destination
 * rectangle is correct, and applying a `naturalWidth / width` factor (as some
 * examples do) would scale the crop twice.
 */
export async function getCroppedBlob(objectUrl: string, area: CropArea): Promise<Blob> {
  // Same object URL the cropper renders, so EXIF orientation is interpreted
  // identically by both and the crop coordinates line up.
  const image = await loadImage(objectUrl)

  const canvas = document.createElement('canvas')
  canvas.width = AVATAR_SIZE
  canvas.height = AVATAR_SIZE
  const context = canvas.getContext('2d')
  if (!context) throw new Error('This browser cannot process images.')

  context.drawImage(
    image,
    Math.round(area.x),
    Math.round(area.y),
    Math.round(area.width),
    Math.round(area.height),
    0,
    0,
    AVATAR_SIZE,
    AVATAR_SIZE,
  )

  const blob = await new Promise<Blob | null>((resolve) => {
    canvas.toBlob(resolve, 'image/webp', 0.9)
  })
  if (!blob) throw new Error('This image could not be processed.')

  // Browsers that cannot *encode* WebP (Safari) return a PNG instead of failing,
  // so the type has to be checked rather than assuming the requested format.
  if (blob.type !== 'image/webp') {
    const jpeg = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob(resolve, 'image/jpeg', 0.9)
    })
    if (jpeg) return jpeg
  }
  return blob
}
