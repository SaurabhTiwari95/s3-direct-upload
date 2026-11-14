import { HttpEventType } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  signal,
} from '@angular/core';
import { Subscription, filter, map, switchMap, tap } from 'rxjs';
import { environment } from '../environments/environment';
import { describeError } from './errors';
import { DownloadLink, UploadRecord, UploadService } from './upload.service';

export type Step = 'idle' | 'requesting' | 'uploading' | 'verifying' | 'done' | 'error';

export const ALLOWED_TYPES: readonly string[] = ['application/pdf', 'image/png', 'image/jpeg'];

@Component({
  selector: 'app-root',
  templateUrl: './app.html',
  styleUrl: './app.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class App {
  private readonly uploads = inject(UploadService);
  private running: Subscription | null = null;

  readonly maxBytes = environment.maxUploadBytes;
  readonly accept = ALLOWED_TYPES.join(',');

  /** Kept in memory only: never written to localStorage or cookies. */
  readonly apiKey = signal('');
  readonly file = signal<File | null>(null);
  readonly step = signal<Step>('idle');
  readonly progress = signal(0);
  readonly error = signal<string | null>(null);
  readonly record = signal<UploadRecord | null>(null);
  readonly link = signal<DownloadLink | null>(null);

  readonly busy = computed(() => ['requesting', 'uploading', 'verifying'].includes(this.step()));
  readonly canUpload = computed(
    () => !this.busy() && this.file() !== null && this.apiKey().trim().length > 0,
  );

  constructor() {
    inject(DestroyRef).onDestroy(() => this.running?.unsubscribe());
  }

  onKeyInput(event: Event): void {
    this.apiKey.set((event.target as HTMLInputElement).value);
  }

  onFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectFile(input.files?.item(0) ?? null);
  }

  /** Quick checks in the browser for a friendly message. The API and the bucket enforce them too. */
  selectFile(file: File | null): void {
    this.reset();
    this.file.set(null);
    if (file === null) {
      return;
    }
    if (!ALLOWED_TYPES.includes(file.type)) {
      this.fail('Only PDF, PNG and JPEG files can be uploaded.');
      return;
    }
    if (file.size === 0) {
      this.fail('The file is empty.');
      return;
    }
    if (file.size > this.maxBytes) {
      this.fail(`The file is larger than ${formatBytes(this.maxBytes)}.`);
      return;
    }
    this.file.set(file);
  }

  upload(): void {
    const file = this.file();
    const apiKey = this.apiKey().trim();
    if (file === null || apiKey === '' || this.busy()) {
      return;
    }
    this.reset();
    this.step.set('requesting');

    this.running = this.uploads
      .start(apiKey, file)
      .pipe(
        switchMap((started) => {
          this.step.set('uploading');
          return this.uploads.sendToStorage(started.upload, file).pipe(
            tap((event) => {
              if (event.type === HttpEventType.UploadProgress) {
                const total = event.total ?? file.size;
                this.progress.set(
                  total > 0 ? Math.min(100, Math.round((100 * event.loaded) / total)) : 0,
                );
              }
            }),
            filter((event) => event.type === HttpEventType.Response),
            map(() => started.id),
          );
        }),
        switchMap((id) => {
          this.progress.set(100);
          this.step.set('verifying');
          return this.uploads.complete(apiKey, id);
        }),
        switchMap((record) =>
          this.uploads.downloadLink(apiKey, record.id).pipe(map((link) => ({ record, link }))),
        ),
      )
      .subscribe({
        next: ({ record, link }) => {
          this.record.set(record);
          this.link.set(link);
          this.step.set('done');
        },
        error: (err: unknown) => this.fail(describeError(err)),
      });
  }

  /** Download links live for a minute; this fetches a fresh one. */
  refreshLink(): void {
    const record = this.record();
    if (record === null) {
      return;
    }
    this.uploads.downloadLink(this.apiKey().trim(), record.id).subscribe({
      next: (link) => this.link.set(link),
      error: (err: unknown) => this.fail(describeError(err)),
    });
  }

  formatBytes = formatBytes;

  private reset(): void {
    this.running?.unsubscribe();
    this.running = null;
    this.step.set('idle');
    this.progress.set(0);
    this.error.set(null);
    this.record.set(null);
    this.link.set(null);
  }

  private fail(message: string): void {
    this.error.set(message);
    this.step.set('error');
  }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
