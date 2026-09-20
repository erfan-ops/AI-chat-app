/**
 * Cloudinary uploads.
 *
 * The image goes straight from the browser to Cloudinary: this app only asks the
 * backend for a signature (which keeps the API secret server-side) and then posts
 * the processed file to Cloudinary with it.
 */

import { apiRequest } from './client'
import type { UploadSignature } from '../types/api'

/** Ask the API to sign an avatar upload (503 when Cloudinary is not configured). */
export function requestUploadSignature(): Promise<UploadSignature> {
  return apiRequest<UploadSignature>('/cloudinary/signature', { method: 'POST' })
}

/** Cloudinary reports upload failures as `{"error": {"message": "..."}}`. */
function cloudinaryErrorMessage(body: unknown, status: number): string {
  if (typeof body === 'object' && body !== null) {
    const error = (body as { error?: { message?: unknown } }).error
    if (error && typeof error.message === 'string') return error.message
  }
  return `Image upload failed (${status})`
}

/**
 * Upload the processed avatar and return its `secure_url`.
 *
 * This bypasses `apiRequest` on purpose: that helper stringifies the body and
 * prefixes the API base URL, so it cannot carry multipart data to another host.
 * `folder`, `timestamp` and `signature` are the exact parameters the backend
 * signed — changing any of them makes Cloudinary reject the upload.
 */
export async function uploadToCloudinary(
  blob: Blob,
  signature: UploadSignature,
  signal?: AbortSignal,
): Promise<string> {
  const form = new FormData()
  form.append('file', blob, blob.type === 'image/webp' ? 'avatar.webp' : 'avatar.jpg')
  form.append('api_key', signature.api_key)
  form.append('timestamp', String(signature.timestamp))
  form.append('signature', signature.signature)
  form.append('folder', signature.folder)

  const response = await fetch(
    `https://api.cloudinary.com/v1_1/${signature.cloud_name}/image/upload`,
    // No Content-Type header: the browser sets the multipart boundary itself.
    { method: 'POST', body: form, signal },
  )
  const body: unknown = await response.json().catch(() => null)

  if (!response.ok) throw new Error(cloudinaryErrorMessage(body, response.status))

  const secureUrl = (body as { secure_url?: unknown } | null)?.secure_url
  if (typeof secureUrl !== 'string' || secureUrl === '') {
    throw new Error('Cloudinary did not return an image URL.')
  }
  return secureUrl
}
