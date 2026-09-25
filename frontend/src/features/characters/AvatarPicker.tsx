import { useCallback, useEffect, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import Cropper from 'react-easy-crop'
import type { Area } from 'react-easy-crop'
import { requestUploadSignature, uploadToCloudinary } from '../../api/cloudinary'
import { getCroppedBlob, AVATAR_SIZE } from '../../utils/cropImage'
import type { UploadKind } from '../../types/api'
import { errorMessage } from '../../utils/errors'
import { pushToast } from '../../components/toastStore'
import { Spinner } from '../../components/Spinner'
import { Avatar } from '../../components/Avatar'
import { PlusIcon, TrashIcon } from '../../components/Icons'
import styles from './AvatarPicker.module.css'

/** Formats the cropper can render and the canvas can read back. Deliberately not
 *  `image/*`: SVG can taint the canvas, and HEIC often will not decode at all. */
const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']
const ACCEPT_ATTRIBUTE = ACCEPTED_TYPES.join(',')
/** Browsers handle far bigger files, but decoding a huge photo just to downscale
 *  it to 512px is slow enough to look broken — reject early with a clear reason. */
const MAX_SOURCE_BYTES = 15 * 1024 * 1024

type PickerStage = 'idle' | 'cropping' | 'uploading' | 'uploaded'

export interface AvatarPickerProps {
  /** The uploaded avatar URL, or null while there is none. */
  value: string | null
  /** Called with the Cloudinary URL once an upload succeeds, or null on remove. */
  onChange: (url: string | null) => void
  /** True while an upload is in flight, so the surrounding form can block submit. */
  onUploadingChange: (uploading: boolean) => void
  /** True while the crop view is open, so the dialog does not close mid-edit. */
  onCroppingChange: (cropping: boolean) => void
  /** Mirrors the form's busy state (e.g. the character is being created). */
  disabled?: boolean
  name: string
  /** What the upload is for: a character's avatar or the signed-in user's picture.
   *  The API maps it to a Cloudinary folder, so the file does not land in the
   *  characters' folder when it is somebody's profile picture. */
  kind: UploadKind
}

/**
 * Avatar field: choose an image, crop it 1:1, and upload it straight to
 * Cloudinary. The crop is rendered to a 512×512 file in the browser; only that
 * file is uploaded, and only after the user confirms the crop.
 */
export function AvatarPicker({
  value,
  onChange,
  onUploadingChange,
  onCroppingChange,
  disabled = false,
  name,
  kind,
}: AvatarPickerProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [stage, setStage] = useState<PickerStage>(value ? 'uploaded' : 'idle')
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [crop, setCrop] = useState({ x: 0, y: 0 })
  const [zoom, setZoom] = useState(1)
  const [croppedArea, setCroppedArea] = useState<Area | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Revoke the object URL of the image being cropped when it is replaced or the
  // component goes away, so abandoned selections do not leak blobs.
  useEffect(() => {
    return () => {
      if (imageUrl) URL.revokeObjectURL(imageUrl)
    }
  }, [imageUrl])

  useEffect(() => {
    return () => abortRef.current?.abort()
  }, [])

  const busy = stage === 'uploading' || disabled
  const selectedName = value

  function handlePick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Reset immediately: picking the same file twice must fire `change` again.
    event.target.value = ''
    if (!file) return

    if (!ACCEPTED_TYPES.includes(file.type)) {
      pushToast('error', 'Please choose a PNG, JPEG, WebP or GIF image.')
      return
    }
    if (file.size > MAX_SOURCE_BYTES) {
      pushToast('error', 'That image is larger than 15 MB — please choose a smaller one.')
      return
    }

    setImageUrl(URL.createObjectURL(file))
    setCrop({ x: 0, y: 0 })
    setZoom(1)
    setCroppedArea(null)
    setStage('cropping')
    onCroppingChange(true)
  }

  const cancelCrop = useCallback(() => {
    if (imageUrl) URL.revokeObjectURL(imageUrl)
    setImageUrl(null)
    setStage(value ? 'uploaded' : 'idle')
    onCroppingChange(false)
  }, [imageUrl, value, onCroppingChange])

  // Escape cancels the crop rather than letting the dialog close and throw the
  // form away. The capture phase is what makes this work: Modal listens on
  // `document` while bubbling, so stopping propagation here keeps the key from
  // ever reaching it. While an upload is in flight Escape does nothing.
  useEffect(() => {
    if (!imageUrl || stage === 'uploading') return
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      cancelCrop()
    }
    document.addEventListener('keydown', handleKeyDown, true)
    return () => document.removeEventListener('keydown', handleKeyDown, true)
  }, [imageUrl, stage, cancelCrop])

  async function confirmCrop() {
    if (!imageUrl || !croppedArea) return
    setStage('uploading')
    onCroppingChange(false)
    onUploadingChange(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const blob = await getCroppedBlob(imageUrl, croppedArea)
      const signature = await requestUploadSignature(kind)
      const secureUrl = await uploadToCloudinary(blob, signature, controller.signal)

      if (imageUrl) URL.revokeObjectURL(imageUrl)
      setImageUrl(null)
      setStage('uploaded')
      onChange(secureUrl)
    } catch (error) {
      if (controller.signal.aborted) return
      // Keep the picked image so the user can retry without re-selecting it.
      setStage('cropping')
      onCroppingChange(true)
      pushToast('error', errorMessage(error))
    } finally {
      abortRef.current = null
      onUploadingChange(false)
    }
  }

  function removeAvatar() {
    onChange(null)
    setStage('idle')
  }

  return (
    <div className={styles.picker}>
      {imageUrl ? (
        <>
          <div className={styles.cropArea}>
            <Cropper
              image={imageUrl}
              crop={crop}
              zoom={zoom}
              aspect={1}
              cropShape="round"
              showGrid={false}
              onCropChange={setCrop}
              onZoomChange={setZoom}
              onCropComplete={(_area, areaPixels) => setCroppedArea(areaPixels)}
            />
          </div>

          <label className={styles.zoomRow}>
            Zoom
            <input
              type="range"
              min={1}
              max={3}
              step={0.01}
              value={zoom}
              onChange={(event) => setZoom(Number(event.target.value))}
              className={styles.zoom}
              disabled={busy}
            />
          </label>

          <div className={styles.actions}>
            <button
              type="button"
              className={styles.ghost}
              onClick={cancelCrop}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="button"
              className={styles.primary}
              onClick={() => void confirmCrop()}
              disabled={busy || !croppedArea}
            >
              {stage === 'uploading' ? (
                <>
                  <Spinner size={14} label="Uploading avatar" className={styles.spinner} />
                  Uploading…
                </>
              ) : (
                'Use this image'
              )}
            </button>
          </div>
          <p className={styles.hint}>
            Drag to reposition, then save. The avatar is stored as {AVATAR_SIZE}×{AVATAR_SIZE}.
          </p>
        </>
      ) : (
        <div className={styles.current}>
          <Avatar name={name} src={value} size={72} />
          <div className={styles.currentText}>
            {value ? (
              <span className={styles.currentName}>Avatar ready</span>
            ) : (
              <span className={styles.currentHint}>No avatar yet — optional</span>
            )}
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.primary}
                onClick={() => inputRef.current?.click()}
                disabled={busy}
              >
                <PlusIcon aria-hidden="true" />
                {value ? 'Replace' : 'Choose image'}
              </button>
              {selectedName && (
                <button
                  type="button"
                  className={styles.ghost}
                  onClick={removeAvatar}
                  disabled={busy}
                  aria-label="Remove avatar"
                >
                  <TrashIcon aria-hidden="true" />
                  Remove
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTRIBUTE}
        onChange={handlePick}
        className={styles.fileInput}
        aria-label="Avatar image file"
        disabled={busy}
      />
    </div>
  )
}
