// The application page's fields, one descriptor each, keyed as the adapter names them.
() => {
  const slug = s => s.toLowerCase().normalize('NFKD').replace(/[^\w]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);
  const seen = {};
  const out = [];
  for (const box of document.querySelectorAll('.RCMFormField')) {
    if (!box.offsetParent || box.closest('[id$="_bgElementContainer"]')) continue;
    const labelEl = box.querySelector('label.rcmFormFieldLabel, .rcmFormFieldLabel');
    const label = (labelEl ? labelEl.innerText : '').replace(/\*/g, '').replace(/\s+/g, ' ').trim();
    const required = !!box.querySelector('.requiredField');
    const d = { label, required, kind: 'unknown', value: '', options: null, id: null, owns: null, name: null };
    const picklist = box.querySelector('input.rcmpaginatedselectinput');
    const text = box.querySelector('input[data-testid="sfTextField"], input.fd-input[type="text"]');
    const area = box.querySelector('textarea');
    const select = box.querySelector('select');
    const radios = [...box.querySelectorAll('input[type="radio"]')];
    const check = box.querySelector('input[type="checkbox"]');
    if (box.classList.contains('attachmentField')) {
      d.kind = 'file';
      d.files = [...box.querySelectorAll('[id$="_attachDownloadLabelLink"]')].filter(e => e.offsetParent).map(e => e.innerText.trim());
      d.value = d.files.length ? d.files[d.files.length - 1] : '';
      const icon = [...box.querySelectorAll('[id$="_attachIcon"]')].find(e => e.offsetParent);
      d.multiple = !!icon && icon.classList.contains('removeAttachments'); // it holds a file
      d.id = icon ? icon.id : null;
    } else if (picklist) {
      d.kind = 'picklist';
      d.id = picklist.id;
      d.owns = picklist.getAttribute('aria-owns');
      d.placeholder = picklist.placeholder; // its empty item's text, in the page's language
      const opener = box.querySelector('button.rcmpaginatedselectbutton');
      d.disabled = !!opener && opener.disabled; // a cascading list before its parent is chosen
      d.value = picklist.value === picklist.placeholder ? '' : picklist.value;
      d.required = d.required || picklist.getAttribute('aria-required') === 'true';
    } else if (radios.length) {
      d.kind = 'radio';
      d.options = radios.map(r => {
        const l = r.labels && r.labels[0] ? r.labels[0].innerText : (r.closest('label') || r.parentElement).innerText;
        return l.trim();
      });
      d.ids = radios.map(r => r.id);
      const on = radios.findIndex(r => r.checked);
      d.value = on >= 0 ? d.options[on] : '';
    } else if (select) {
      d.kind = 'select';
      d.id = select.id;
      d.options = [...select.options].filter(o => o.value).map(o => o.text.trim());
      d.value = select.value ? select.options[select.selectedIndex].text.trim() : '';
    } else if (area) {
      d.kind = 'text';
      d.id = area.id;
      d.name = area.name;
      d.value = area.value;
      d.max = area.maxLength > 0 ? area.maxLength : null;
    } else if (text) {
      d.kind = text.closest('[class*="DatePicker"], [class*="datePicker"], .sfDateInput') ? 'date' : 'text';
      d.id = text.id;
      d.name = text.getAttribute('name');
      d.value = text.value;
      d.max = text.maxLength > 0 ? text.maxLength : null;
      d.required = d.required || text.getAttribute('aria-required') === 'true';
    } else if (check) {
      d.kind = 'checkbox';
      d.id = check.id;
      d.value = check.checked ? 'Yes' : 'No';
    } else if (!box.querySelector('input, select, textarea')) {
      continue; // a label alone: a heading or an instruction
    }
    let key = d.name || slug(label) || d.kind;
    seen[key] = (seen[key] || 0) + 1;
    if (seen[key] > 1) key += '-' + seen[key];
    d.key = key;
    out.push(d);
  }
  return out;
}
