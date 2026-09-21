/**
 * Cloudinary delivery helpers.
 *
 * Avatars are stored as the upload's `secure_url` (a 512×512 master) and resized
 * on delivery, so a 30px message avatar never downloads the master. Anything that
 * is not a Cloudinary upload URL — avatars stored before uploads existed, a local
 * object URL while cropping — is returned untouched.
 */

const CLOUDINARY_HOST = 'https://res.cloudinary.com/'
const UPLOAD_MARKER = '/image/upload/'

/**
 * The Cloudinary delivery URL for an avatar displayed at `size` pixels.
 *
 * `c_fill` with equal width/height keeps the output square whatever the source
 * aspect is; `q_auto`/`f_auto` let Cloudinary pick the quality and the best format
 * the browser accepts (WebP/AVIF/JPEG XL), as its own delivery guidance suggests:
 * the two optimisation parameters are their own URL components.
 *
 * Returns '' when there is no avatar, so callers can treat it as "no image".
 */
export function getCloudinaryAvatarUrl(url: string | null | undefined, size: number): string {
  if (!url) return ''
  if (!url.startsWith(CLOUDINARY_HOST)) return url
  const marker = url.indexOf(UPLOAD_MARKER)
  if (marker === -1) return url

  const px = Math.max(1, Math.round(size*2)) // temporary double the size to not lose quality when the page is zoomed in
  const after = marker + UPLOAD_MARKER.length
  return `${url.slice(0, after)}c_fill,h_${px},w_${px}/q_auto/f_auto/${url.slice(after)}`
}
