import { HttpEventType, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { App, formatBytes } from './app';
import { API_BASE_URL } from './upload.service';

const API = 'http://api.test';
const ID = 'c'.repeat(32);

describe('App', () => {
  let fixture: ComponentFixture<App>;
  let app: App;
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: API },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(App);
    app = fixture.componentInstance;
    http = TestBed.inject(HttpTestingController);
    await fixture.whenStable();
  });

  afterEach(() => http.verify());

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  it('renders the page with the upload button disabled', () => {
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('h1')?.textContent).toContain('Direct upload to S3');
    expect(el.querySelector('button')?.disabled).toBe(true);
  });

  it('refuses files of the wrong type, empty files and files over the limit', () => {
    app.selectFile(new File(['x'], 'a.exe', { type: 'application/x-msdownload' }));
    expect(app.error()).toContain('Only PDF, PNG and JPEG');
    expect(app.file()).toBeNull();

    app.selectFile(new File([], 'empty.pdf', { type: 'application/pdf' }));
    expect(app.error()).toBe('The file is empty.');

    const big = new File(['x'], 'big.pdf', { type: 'application/pdf' });
    Object.defineProperty(big, 'size', { value: app.maxBytes + 1 });
    app.selectFile(big);
    expect(app.error()).toContain('larger than');
  });

  it('runs the whole flow: sign, upload with progress, verify, link', async () => {
    const file = new File(['%PDF-1.7 sample'], 'invoice.pdf', { type: 'application/pdf' });
    app.apiKey.set('key-123');
    app.selectFile(file);
    expect(app.canUpload()).toBe(true);

    app.upload();
    expect(app.step()).toBe('requesting');
    http.expectOne(`${API}/v1/uploads`).flush({
      id: ID,
      key: `uploads/2027/01/${ID}.pdf`,
      upload: { url: 'https://bucket.test/', fields: { key: `uploads/2027/01/${ID}.pdf` } },
      expires_at: '2027-01-01T00:05:00Z',
    });

    expect(app.step()).toBe('uploading');
    const toStorage = http.expectOne('https://bucket.test/');
    toStorage.event({ type: HttpEventType.UploadProgress, loaded: 5, total: 10 });
    expect(app.progress()).toBe(50);
    await fixture.whenStable();
    expect(text()).toContain('Uploading to storage: 50%');
    toStorage.flush('', { status: 204, statusText: 'No Content' });

    expect(app.step()).toBe('verifying');
    http.expectOne(`${API}/v1/uploads/${ID}/complete`).flush({
      id: ID,
      filename: 'invoice.pdf',
      content_type: 'application/pdf',
      size: file.size,
      status: 'complete',
      reason: null,
      created_at: '2027-01-01T00:00:00Z',
      completed_at: '2027-01-01T00:00:01Z',
    });
    http
      .expectOne(`${API}/v1/uploads/${ID}/download`)
      .flush({ url: 'https://bucket.test/download', expires_at: '2027-01-01T00:01:01Z' });

    expect(app.step()).toBe('done');
    await fixture.whenStable();
    expect(text()).toContain('Uploaded and verified');
    const link = (fixture.nativeElement as HTMLElement).querySelector('a');
    expect(link?.getAttribute('href')).toBe('https://bucket.test/download');
  });

  it('shows a clear error when the upload is rejected', async () => {
    app.apiKey.set('key-123');
    app.selectFile(new File(['MZ'], 'fake.pdf', { type: 'application/pdf' }));
    app.upload();
    http.expectOne(`${API}/v1/uploads`).flush({
      id: ID,
      key: 'k',
      upload: { url: 'https://bucket.test/', fields: {} },
      expires_at: 'x',
    });
    http.expectOne('https://bucket.test/').flush('', { status: 204, statusText: 'No Content' });
    http
      .expectOne(`${API}/v1/uploads/${ID}/complete`)
      .flush(
        { error: 'upload_rejected', details: { reason: 'signature_mismatch' } },
        { status: 422, statusText: 'Unprocessable Entity' },
      );

    expect(app.step()).toBe('error');
    await fixture.whenStable();
    const alert = (fixture.nativeElement as HTMLElement).querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('not really that file type');
  });

  it('shows a clear error when the API key is wrong', () => {
    app.apiKey.set('wrong');
    app.selectFile(new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' }));
    app.upload();
    http
      .expectOne(`${API}/v1/uploads`)
      .flush({ error: 'unauthorized' }, { status: 401, statusText: 'Unauthorized' });
    expect(app.error()).toBe('The API key was not accepted.');
  });

  it('formats sizes', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(10 * 1024 * 1024)).toBe('10.0 MB');
  });
});
