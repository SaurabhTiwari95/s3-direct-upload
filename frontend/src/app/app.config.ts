import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';

export const appConfig: ApplicationConfig = {
  // The default XHR backend reports upload progress; withFetch() would not.
  providers: [provideBrowserGlobalErrorListeners(), provideHttpClient()],
};
