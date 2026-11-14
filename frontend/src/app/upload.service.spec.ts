import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { API_BASE_URL, StartedUpload, UploadService } from './upload.service';

const API = 'http://api.test';
const KEY = 'test-api-key';

describe('UploadService', () => {
  let service: UploadService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: API },
      ],
    });
    service = TestBed.inject(UploadService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('asks the API for a pre-signed POST with name, type and exact size', () => {
    const file = new File(['%PDF-1.7 hello'], 'invoice.pdf', { type: 'application/pdf' });
    let result: StartedUpload | undefined;
    service.start(KEY, file).subscribe((r) => (result = r));

    const req = http.expectOne(`${API}/v1/uploads`);
    expect(req.request.method).toBe('POST');
    expect(req.request.headers.get('X-Api-Key')).toBe(KEY);
    expect(req.request.body).toEqual({
      filename: 'invoice.pdf',
      content_type: 'application/pdf',
      size: file.size,
    });
    const body: StartedUpload = {
      id: 'a'.repeat(32),
      key: 'uploads/2027/01/x.pdf',
      upload: { url: 'https://bucket.test', fields: { key: 'uploads/2027/01/x.pdf' } },
      expires_at: '2027-01-01T00:05:00Z',
    };
    req.flush(body);
    expect(result).toEqual(body);
  });

  it('sends the file to storage as a form: policy fields first, file last, no API key', () => {
    const file = new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' });
    const fields = { key: 'uploads/k.pdf', 'Content-Type': 'application/pdf', policy: 'p' };
    service.sendToStorage({ url: 'https://bucket.test/', fields }, file).subscribe();

    const req = http.expectOne('https://bucket.test/');
    expect(req.request.method).toBe('POST');
    expect(req.request.headers.has('X-Api-Key')).toBe(false);
    expect(req.request.reportProgress).toBe(true);

    const form = req.request.body as FormData;
    const names: string[] = [];
    form.forEach((_value, name) => names.push(name));
    expect(names).toEqual(['key', 'Content-Type', 'policy', 'file']);
    expect(form.get('Content-Type')).toBe('application/pdf');
    expect((form.get('file') as File).name).toBe('a.pdf');
    req.flush('', { status: 204, statusText: 'No Content' });
  });

  it('completes and fetches a download link with the API key', () => {
    const id = 'b'.repeat(32);
    service.complete(KEY, id).subscribe();
    const done = http.expectOne(`${API}/v1/uploads/${id}/complete`);
    expect(done.request.method).toBe('POST');
    expect(done.request.headers.get('X-Api-Key')).toBe(KEY);
    done.flush({});

    service.downloadLink(KEY, id).subscribe();
    const link = http.expectOne(`${API}/v1/uploads/${id}/download`);
    expect(link.request.method).toBe('GET');
    expect(link.request.headers.get('X-Api-Key')).toBe(KEY);
    link.flush({ url: 'https://bucket.test/x', expires_at: '2027-01-01T00:01:00Z' });
  });
});
