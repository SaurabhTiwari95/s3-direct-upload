import { HttpErrorResponse } from '@angular/common/http';
import { describeError } from './errors';

function httpError(status: number, error: unknown): HttpErrorResponse {
  return new HttpErrorResponse({ status, error });
}

describe('describeError', () => {
  it('explains network and CORS failures', () => {
    expect(describeError(httpError(0, null))).toContain('Could not reach the server');
  });

  it('maps API error codes to sentences', () => {
    expect(describeError(httpError(401, { error: 'unauthorized' }))).toBe(
      'The API key was not accepted.',
    );
    expect(describeError(httpError(429, { error: 'rate_limited' }))).toContain('Too many requests');
    expect(describeError(httpError(418, { error: 'something_new' }))).toContain('(418)');
  });

  it('explains why an upload was rejected', () => {
    const err = httpError(422, {
      error: 'upload_rejected',
      details: { reason: 'signature_mismatch' },
    });
    expect(describeError(err)).toBe(
      'The upload was rejected: its content is not really that file type.',
    );
    expect(describeError(httpError(422, { error: 'upload_rejected' }))).toContain(
      'failed verification',
    );
  });

  it('recognises S3 XML errors', () => {
    const xml = '<Error><Code>EntityTooLarge</Code></Error>';
    expect(describeError(httpError(400, xml))).toContain('larger than the size that was signed');
    expect(describeError(httpError(403, '<Error><Code>AccessDenied</Code></Error>'))).toBe(
      'Storage or server error (403).',
    );
  });

  it('handles anything else', () => {
    expect(describeError(new Error('boom'))).toBe('Something went wrong. Try again.');
  });
});
