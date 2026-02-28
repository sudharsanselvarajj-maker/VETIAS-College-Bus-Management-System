/* 
  VET IAS Bus System - Core Logic 
  Handles Device Binding, GPS Tracking, and QR Scanning
*/

const Utils = {
    // 1. Device Fingerprinting (Simple Version)
    getDeviceId: async () => {
        const components = [
            navigator.userAgent,
            navigator.language,
            screen.colorDepth,
            screen.width + 'x' + screen.height,
            new Date().getTimezoneOffset()
        ];
        const str = components.join('###');

        // Simple Hash Function (SHA-256 would be better in prod)
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            const char = str.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash; // Convert to 32bit integer
        }
        return 'DEV-' + Math.abs(hash);
    },

    // 2. Geolocation Wrapper (Robust with Retry)
    getLocation: () => {
        return new Promise((resolve, reject) => {
            if (!navigator.geolocation) {
                reject("Geolocation not supported");
                return;
            }

            const optionsHigh = { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 };
            const optionsLow = { enableHighAccuracy: false, timeout: 10000, maximumAge: 0 };

            // Attempt 1: High Accuracy
            navigator.geolocation.getCurrentPosition(
                (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
                (err) => {
                    console.warn("High Accuracy Geo failed, trying low accuracy...", err);
                    // Attempt 2: Low Accuracy (Fallback)
                    navigator.geolocation.getCurrentPosition(
                        (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
                        (err2) => {
                            let msg = "Location Access Denied or Unavailable";
                            if (err2.code === 3) msg = "GPS Signal Timeout. Please move outdoors.";
                            else if (err2.code === 1) msg = "Location Permission Denied.";
                            reject(msg);
                        },
                        optionsLow
                    );
                },
                optionsHigh
            );
        });
    },

    // 3. Proactive Location Check
    verifyLocation: async () => {
        try {
            await Utils.getLocation();
            showToast("Location synchronized.", "success");
            return true;
        } catch (e) {
            showToast("Location required: " + e, "error");
            return false;
        }
    }
};

// Driver Module Logic
const DriverApp = {
    timer: null,

    startTracking: (busNo) => {
        if (DriverApp.timer) return;

        console.log("Starting GPS Tracking for " + busNo);
        showToast("GPS Tracking Started", "success");

        DriverApp.timer = setInterval(async () => {
            try {
                const loc = await Utils.getLocation();
                document.getElementById('status-text').innerText = `Lat: ${loc.lat.toFixed(4)}, Lng: ${loc.lng.toFixed(4)}`;

                // Send to Server
                await fetch('/api/driver-heartbeat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bus_no: busNo, lat: loc.lat, lng: loc.lng })
                });

            } catch (e) {
                console.error("GPS Error", e);
                const statusEl = document.getElementById('status-text');
                if (statusEl) statusEl.innerText = "GPS Error: " + e.message;
            }
        }, 10000); // Every 10 seconds
    },

    startAutoSync: (busNo) => {
        if (!navigator.geolocation) { showToast("GPS Permission Needed", "error"); return; }

        // Watch Position (High Freq) - Just stores locally
        navigator.geolocation.watchPosition(pos => {
            DriverApp.currentLat = pos.coords.latitude;
            DriverApp.currentLng = pos.coords.longitude;
        }, err => console.warn(err), { enableHighAccuracy: true });

        // Heartbeat (Low Freq) - Sends to server
        setInterval(() => {
            if (!DriverApp.currentLat) return;
            fetch('/api/driver-heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bus_no: busNo, lat: DriverApp.currentLat, lng: DriverApp.currentLng })
            }).then(r => r.json()).then(d => {
                const statusEl = document.getElementById('sync-status');
                if (statusEl) statusEl.innerText = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            }).catch(e => console.error(e));
        }, 5000);
    },

    startManifestSync: () => {
        let lastManifestCount = 0;
        const sync = () => {
            fetch('/api/bus-manifest').then(r => r.json()).then(d => {
                const countEl = document.getElementById('student-count');
                if (countEl) countEl.innerText = `${d.count} / 40`;

                const list = document.getElementById('manifest-list');
                if (!list) return;

                if (d.count > lastManifestCount) {
                    showToast(`New student boarded.`, 'success');
                    lastManifestCount = d.count;
                }

                if (d.count === 0) {
                    list.innerHTML = `<div class="text-center py-5"><i class="bi bi-inbox fs-1 text-muted d-block"></i><p class="text-muted mt-3">No students on board yet.</p></div>`;
                    return;
                }

                list.innerHTML = d.manifest.map(s => `
                    <div class="manifest-item animate__animated animate__fadeIn">
                        <div class="d-flex align-items-center">
                            <div class="stat-icon me-3" style="width: 40px; height: 40px; font-size: 1rem;">
                                ${s.student_name ? s.student_name.charAt(0) : '?'}
                            </div>
                            <div class="manifest-info">
                                <h6>${s.student_name}</h6>
                                <p><i class="bi bi-clock me-1"></i> ${s.timestamp}</p>
                            </div>
                        </div>
                        <span class="badge rounded-pill bg-light text-dark border px-3">
                            ${s.method}
                        </span>
                    </div>
                `).join('');
            });
        };
        setInterval(sync, 5000); // 5 seconds
        sync(); // Initial call
    },

    startQRGen: () => {
        const REFRESH_RATE = 10; // Seconds
        let timeLeft = REFRESH_RATE;

        // Visual Countdown (Updates every 1s)
        setInterval(() => {
            timeLeft--;
            const timerEl = document.getElementById('qr-timer');
            if (timerEl) timerEl.innerText = `${timeLeft}s`;

            if (timeLeft <= 0) timeLeft = REFRESH_RATE;
        }, 1000);

        // Actual QR Logic (Runs every 10s)
        const generate = async () => {
            try {
                const res = await fetch('/api/get-qr');
                const data = await res.json();

                // Clear old QR
                document.getElementById("qrcode").innerHTML = "";

                // Calculate size (Responsive for mobile)
                const qrSize = window.innerWidth < 480 ? 180 : 250;

                // Generate New
                new QRCode(document.getElementById("qrcode"), {
                    text: data.qr_data,
                    width: qrSize,
                    height: qrSize
                });

                // Reset Timer visually to be sync
                timeLeft = REFRESH_RATE;
                if (document.getElementById('qr-timer')) document.getElementById('qr-timer').innerText = `${REFRESH_RATE}s`;

            } catch (e) { console.error(e); }
        };

        setInterval(generate, REFRESH_RATE * 1000);
        generate(); // Initial call
    }
};

// Student Module Logic
const StudentApp = {
    scanner: null,

    startScanner: () => {
        StudentApp.scanner = new Html5Qrcode("qr-reader");

        const config = { fps: 10, qrbox: 250 };
        const onSuccess = async (decodedText, decodedResult) => {
            // On Success
            StudentApp.scanner.stop();
            document.getElementById('qr-reader').style.display = 'none';
            showToast("QR Scanned! Verifying...", "info");

            // Get Location for Geofence
            try {
                const loc = await Utils.getLocation();
                StudentApp.markAttendance(decodedText, loc);
            } catch (e) {
                showToast("Location required for attendance!", "error");
            }
        };

        // Attempt 1: Back Camera
        StudentApp.scanner.start(
            { facingMode: "environment" },
            config,
            onSuccess
        ).catch(err => {
            console.warn("Environment camera failed (likely desktop), trying user camera...", err);
            // Attempt 2: Front Camera (Fallback)
            StudentApp.scanner.start(
                { facingMode: "user" },
                config,
                onSuccess
            ).catch(err2 => {
                showToast("Camera Error: " + err2, "error");
            });
        });
    },

    markAttendance: async (qrData, loc) => {
        const deviceId = await Utils.getDeviceId();
        const res = await fetch('/api/mark-attendance', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                qr_data: qrData,
                lat: loc.lat,
                lng: loc.lng,
                device_id: deviceId
            })
        });

        const data = await res.json();
        if (data.status === 'success') {
            const resultArea = document.getElementById('result-area');
            if (resultArea) {
                resultArea.innerHTML = `
                    <div class="card border-success bg-success bg-opacity-10 text-success p-3">
                        <h4 class="mb-1"><i class="bi bi-check-circle-fill"></i> Attendance Marked!</h4>
                        <p class="mb-0 small">Bus verified & location confirmed.</p>
                    </div>
                `;
            }
            showToast(data.message, "success");
            setTimeout(() => location.reload(), 2000);
        } else {
            showToast(data.message, "error");
            const resultArea = document.getElementById('result-area');
            if (resultArea) {
                resultArea.innerHTML = `
                     <div class="card border-danger bg-danger bg-opacity-10 text-danger p-3">
                        <h4 class="mb-1"><i class="bi bi-exclamation-triangle-fill"></i> Failed!</h4>
                        <p class="mb-0 small">${data.message}</p>
                        <button class="btn btn-sm btn-outline-danger mt-2" onclick="location.reload()">Try Again</button>
                    </div>
                `;
            }
        }
    }
};

// --- Centralized Toast & UI Styles ---

// Inject Toast CSS
const style = document.createElement('style');
style.innerHTML = `
    #toast-container {
        position: fixed;
        top: 20px;
        right: 20px;
        z-index: 99999;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }
    .toast-msg {
        background: white;
        color: #333;
        padding: 1rem 1.5rem;
        border-radius: 12px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.15);
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 300px;
        border-left: 5px solid #2ecc71;
        transform: translateX(100%);
        animation: slideInRight 0.4s forwards;
    }
    .toast-msg.error { border-left-color: #e74c3c; }
    .toast-msg.info { border-left-color: #3498db; }
    
    @keyframes slideInRight {
        to { transform: translateX(0); }
    }
    @keyframes fadeOutRight {
        to { transform: translateX(100%); opacity: 0; }
    }
`;
document.head.appendChild(style);

// Helper: Ensure container exists
function getToastContainer() {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }
    return container;
}

// Global Toast function
function showToast(message, type = 'success') {
    const container = getToastContainer();
    const d = document.createElement('div');
    d.className = `toast-msg ${type}`;

    // Icons based on type
    let icon = 'bi-check-circle-fill text-success';
    if (type === 'error') icon = 'bi-exclamation-circle-fill text-danger';
    if (type === 'info') icon = 'bi-info-circle-fill text-primary';

    d.innerHTML = `
        <i class="bi ${icon} fs-4"></i>
        <div>
            <div class="fw-bold text-uppercase small" style="letter-spacing:0.5px; opacity:0.7">${type}</div>
            <div class="fw-medium">${message}</div>
        </div>
    `;

    container.appendChild(d);

    // Remove after 3.5s
    setTimeout(() => {
        d.style.animation = 'fadeOutRight 0.4s forwards';
        setTimeout(() => d.remove(), 400);
    }, 3500);
}

// --- GLOBAL MOBILE NAVBAR LOGIC ---
document.addEventListener('DOMContentLoaded', () => {
    // Select all nav links inside the collapse menu
    const navLinks = document.querySelectorAll('.navbar-collapse .nav-link, .navbar-collapse .admin-nav-btn, .navbar-collapse button');
    const navMenu = document.getElementById('navMenu');

    if (navMenu) {
        navLinks.forEach(link => {
            link.addEventListener('click', () => {
                // Check if menu is visibly open (Bootstrap adds 'show' class)
                if (navMenu.classList.contains('show')) {
                    // Try to get existing instance
                    let bsCollapse = bootstrap.Collapse.getInstance(navMenu);

                    if (bsCollapse) {
                        bsCollapse.hide();
                    } else {
                        // Create new instance if none exists
                        const newCollapse = new bootstrap.Collapse(navMenu, { toggle: false });
                        newCollapse.hide();
                    }

                    // Fallback: Force remove class if animation fails or instance issues
                    setTimeout(() => {
                        if (navMenu.classList.contains('show')) {
                            navMenu.classList.remove('show');
                        }
                    }, 350); // Wait for transition
                }
            });
        });
    }
});
