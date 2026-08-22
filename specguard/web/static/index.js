// Landing-page behaviour for SpecGuard.
//
// This file exists because the deployed Content-Security-Policy sets
// `script-src 'self'` and allows no inline script. Every behaviour below is
// advisory: the server revalidates every upload, and no check here changes
// what the runtime accepts.

const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
function describeSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}
function refreshZone(zone) {
  const input = zone.querySelector('input');
  const fileLabel = zone.querySelector('.file');
  const warnLabel = zone.querySelector('.warn');
  const choose = zone.querySelector('.choose');
  const file = input.files && input.files[0];
  if (!file) {
    zone.removeAttribute('data-filled');
    zone.removeAttribute('data-problem');
    fileLabel.textContent = 'No file chosen';
    fileLabel.dataset.empty = 'true';
    warnLabel.hidden = true;
    choose.textContent = 'Choose PDF';
    return;
  }
  zone.setAttribute('data-filled', '');
  fileLabel.textContent = file.name + ' · ' + describeSize(file.size);
  fileLabel.dataset.empty = 'false';
  choose.textContent = 'Replace PDF';
  const problems = [];
  if (file.type !== 'application/pdf') problems.push('This file is not application/pdf.');
  if (file.size > MAX_UPLOAD_BYTES) problems.push('This file is larger than 5 MB.');
  if (problems.length) {
    zone.setAttribute('data-problem', '');
    warnLabel.textContent = problems.join(' ') + ' The server will reject it.';
    warnLabel.hidden = false;
  } else {
    zone.removeAttribute('data-problem');
    warnLabel.hidden = true;
  }
}
for (const zone of document.querySelectorAll('.drop-zone')) {
  const input = zone.querySelector('input');
  for (const eventName of ['dragenter', 'dragover']) zone.addEventListener(eventName, event => { event.preventDefault(); zone.classList.add('dragging'); });
  for (const eventName of ['dragleave', 'drop']) zone.addEventListener(eventName, event => { event.preventDefault(); zone.classList.remove('dragging'); });
  zone.addEventListener('drop', event => { input.files = event.dataTransfer.files; refreshZone(zone); });
  input.addEventListener('change', () => refreshZone(zone));
  refreshZone(zone);
}
const auditForm = document.getElementById('audit-form');
const auditSubmit = document.getElementById('audit-submit');
const auditProgress = document.getElementById('audit-progress');
const auditFields = document.getElementById('audit-fields');
const idleLabel = auditSubmit.textContent;
const idleTitle = document.title;
let submitting = false;
function setIdle() {
  submitting = false;
  auditSubmit.disabled = false;
  auditSubmit.textContent = idleLabel;
  auditProgress.hidden = true;
  auditForm.removeAttribute('aria-busy');
  auditFields.removeAttribute('data-state');
  auditFields.removeAttribute('inert');
  document.title = idleTitle;
}
auditForm.addEventListener('submit', event => {
  if (submitting) { event.preventDefault(); return; }
  submitting = true;
  auditSubmit.disabled = true;
  auditSubmit.textContent = 'Audit running';
  auditProgress.hidden = false;
  auditProgress.focus();
  auditForm.setAttribute('aria-busy', 'true');
  auditFields.setAttribute('data-state', 'submitting');
  auditFields.setAttribute('inert', '');
  document.title = 'Audit running — ' + idleTitle;
});
const sampleGrid = document.getElementById('sample-grid');
const sampleProgress = document.getElementById('sample-progress');
const sampleForms = sampleGrid.querySelectorAll('form');
const sampleButtons = sampleGrid.querySelectorAll('button');
let sampleSubmitting = false;
function setSampleIdle() {
  sampleSubmitting = false;
  for (const button of sampleButtons) button.disabled = false;
  sampleProgress.hidden = true;
  sampleGrid.removeAttribute('aria-busy');
  document.title = idleTitle;
}
for (const sampleForm of sampleForms) {
  sampleForm.addEventListener('submit', event => {
    if (sampleSubmitting) { event.preventDefault(); return; }
    sampleSubmitting = true;
    for (const button of sampleButtons) button.disabled = true;
    sampleProgress.hidden = false;
    sampleProgress.focus();
    sampleGrid.setAttribute('aria-busy', 'true');
    document.title = 'Sample audit running — ' + idleTitle;
  });
}
window.addEventListener('pageshow', event => { if (event.persisted) { setIdle(); setSampleIdle(); } });
const relativeTime = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
const timeUnits = [['second', 60], ['minute', 60], ['hour', 24], ['day', 7], ['week', 4.35], ['month', 12], ['year', Infinity]];
for (const stamp of document.querySelectorAll('time[datetime]')) {
  const moment = new Date(stamp.dateTime);
  if (isNaN(moment.getTime())) continue;
  stamp.title = moment.toLocaleString();
  let value = (moment.getTime() - Date.now()) / 1000;
  let unit = 'second';
  for (const [name, step] of timeUnits) {
    unit = name;
    if (Math.abs(value) < step) break;
    value = value / step;
  }
  stamp.textContent = relativeTime.format(Math.round(value), unit);
}
