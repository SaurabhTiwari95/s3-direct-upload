import { HttpClient, HttpEvent, HttpHeaders } from '@angular/common/http';
import { Injectable, InjectionToken, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../environments/environment';

/** Base URL of the API, e.g. http://127.0.0.1:8000. Overridden in tests. */
export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', {
  providedIn: 'root',
  factory: () => environment.apiBaseUrl,
});

/** The pre-signed POST: where to send the form, and the policy fields to send with the file. */
export interface PresignedPost {
  url: string;
  fields: Record<string, string>;
}

export interface StartedUpload {
  id: string;
  key: string;
  upload: PresignedPost;
  expires_at: string;
}

export interface UploadRecord {
  id: string;
  filename: string;
  content_type: string;
  size: number;
  status: 'pending' | 'complete' | 'rejected';
  reason: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface DownloadLink {
  url: string;
  expires_at: string;
}

@Injectable({ providedIn: 'root' })
export class UploadService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  /** Step 1: the API checks name, type and size, picks the key and signs a POST policy. */
  start(apiKey: string, file: File): Observable<StartedUpload> {
    return this.http.post<StartedUpload>(
      `${this.baseUrl}/v1/uploads`,
      { filename: file.name, content_type: file.type, size: file.size },
      { headers: this.auth(apiKey) },
    );
  }

  /**
   * Step 2: the browser sends the file straight to S3. The API key is NOT sent: S3 trusts the
   * signed policy fields instead. The file must be the last form field, after the policy fields.
   */
  sendToStorage(target: PresignedPost, file: File): Observable<HttpEvent<unknown>> {
    const form = new FormData();
    for (const [name, value] of Object.entries(target.fields)) {
      form.append(name, value);
    }
    form.append('file', file);
    return this.http.post(target.url, form, {
      reportProgress: true,
      observe: 'events',
      responseType: 'text',
    });
  }

  /** Step 3: the API checks the stored object's size, type and file signature. */
  complete(apiKey: string, id: string): Observable<UploadRecord> {
    return this.http.post<UploadRecord>(
      `${this.baseUrl}/v1/uploads/${encodeURIComponent(id)}/complete`,
      {},
      { headers: this.auth(apiKey) },
    );
  }

  /** A short-lived pre-signed GET for a verified upload. */
  downloadLink(apiKey: string, id: string): Observable<DownloadLink> {
    return this.http.get<DownloadLink>(
      `${this.baseUrl}/v1/uploads/${encodeURIComponent(id)}/download`,
      { headers: this.auth(apiKey) },
    );
  }

  private auth(apiKey: string): HttpHeaders {
    return new HttpHeaders({ 'X-Api-Key': apiKey });
  }
}
