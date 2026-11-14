import { HttpErrorResponse } from '@angular/common/http';

const API_MESSAGES: Record<string, string> = {
  unauthorized: 'The API key was not accepted.',
  rate_limited: 'Too many requests. Wait a minute and try again.',
  file_too_large: 'The file is larger than the server allows.',
  extension_does_not_match_type: 'The file extension does not match its type.',
  invalid_request: 'The server did not accept this file (name, type or size).',
  not_uploaded: 'The file has not reached storage yet.',
  upload_expired: 'The upload link expired. Start again.',
  storage_unavailable: 'Storage could not be checked. Try again shortly.',
  body_too_large: 'The request was too large.',
};

const REJECT_REASONS: Record<string, string> = {
  size_mismatch: 'its size differs from what was declared',
  type_mismatch: 'its stored type differs from what was declared',
  signature_mismatch: 'its content is not really that file type',
  expired: 'the upload link expired before the file arrived',
};

/** Turns any failure in the upload flow into one short, user-facing sentence. */
export function describeError(err: unknown): string {
  if (!(err instanceof HttpErrorResponse)) {
    return 'Something went wrong. Try again.';
  }
  if (err.status === 0) {
    return 'Could not reach the server. Check that it is running and that CORS allows this page.';
  }
  const body: unknown = err.error;
  if (body !== null && typeof body === 'object' && 'error' in body) {
    const apiBody = body as { error: unknown; details?: { reason?: unknown } };
    const code = String(apiBody.error);
    if (code === 'upload_rejected') {
      const reason = String(apiBody.details?.reason ?? '');
      return `The upload was rejected: ${REJECT_REASONS[reason] ?? 'it failed verification'}.`;
    }
    return API_MESSAGES[code] ?? `The server refused the request (${err.status}).`;
  }
  // S3 answers with XML, which arrives as a string.
  if (typeof body === 'string' && body.includes('EntityTooLarge')) {
    return 'Storage refused the file: it is larger than the size that was signed.';
  }
  return `Storage or server error (${err.status}).`;
}
