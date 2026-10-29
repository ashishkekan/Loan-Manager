// Appearance is rendered by the server from the administrator setting.
    // ──────────────────────────────────────
    // AUTO-FILL INTEREST RATES BASED ON LOAN TYPE
    // ──────────────────────────────────────
    const loanTypeSelect = document.getElementById('id_loan_type');
    const rateInput = document.getElementById('id_interest_rate');

    const typicalRates = {
        'home': 8.5, 'car': 9.5, 'education': 10.0,
        'personal': 12.0, 'business': 14.0, 'gold': 11.0, 'other': 12.0
    };

    if (loanTypeSelect && rateInput) {
        loanTypeSelect.addEventListener('change', function() {
            const rate = typicalRates[this.value];
            if (rate) {
                rateInput.value = rate;
                rateInput.dispatchEvent(new Event('input')); // Trigger EMI calc
            }
        });
        // Trigger on load if type is already selected
        if (loanTypeSelect.value) {
            rateInput.value = typicalRates[loanTypeSelect.value] || rateInput.value;
        }
    }

    // ──────────────────────────────────────
    // DRAG & DROP FILE UPLOAD ENHANCEMENT
    // ──────────────────────────────────────
    const dropZone = document.getElementById('docDropZone');
    const fileInput = document.getElementById('id_file');

    if (dropZone && fileInput) {
        ['dragenter', 'dragover'].forEach(evt => {
            dropZone.addEventListener(evt, (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
        });
        ['dragleave', 'drop'].forEach(evt => {
            dropZone.addEventListener(evt, (e) => { e.preventDefault(); dropZone.classList.remove('drag-over'); });
        });
        dropZone.addEventListener('drop', (e) => {
            fileInput.files = e.dataTransfer.files;
            updateDropZoneText(e.dataTransfer.files[0].name);
        });
        fileInput.addEventListener('change', () => {
            if(fileInput.files.length) updateDropZoneText(fileInput.files[0].name);
        });

        function updateDropZoneText(name) {
            dropZone.innerHTML = `<i class="fas fa-file-circle-check" style="font-size:32px;color:var(--primary);margin-bottom:8px;display:block;"></i> <strong>${name}</strong><br><small>Click to change</small>`;
        }
    }
    // ──────────────────────────────────────
    // LANDING PAGE — PROCESS TABS (Borrower/Lender)
    // ──────────────────────────────────────
    const procTabs = document.querySelectorAll('.proc-tab');
    const tabContents = document.querySelectorAll('.tab-content');

    procTabs.forEach(tab => {
        tab.addEventListener('click', function() {
            // Remove active from all tabs
            procTabs.forEach(t => t.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));
            
            // Add active to clicked tab
            this.classList.add('active');
            
            // Show corresponding content
            const targetId = 'tab-' + this.dataset.tab;
            document.getElementById(targetId)?.classList.add('active');
        });
    });
