document.addEventListener('DOMContentLoaded', () => {
    // 1. Inject the theme switcher HTML if not already there and page allows it
    if (!document.querySelector('.theme-switcher') && !document.body.classList.contains('no-switcher')) {
        const switcherHTML = `
            <div class="theme-switcher">
                <span>Theme</span>
                <button class="theme-btn" id="globalThemeLightBtn">Light</button>
                <button class="theme-btn" id="globalThemeDarkBtn">Dark</button>
            </div>
        `;
        document.body.insertAdjacentHTML('afterbegin', switcherHTML);
    }

    // 2. Setup logic
    // Look for global switcher buttons first, then fallback to page-specific ones
    const getBtn = (id) => document.getElementById(id);
    const themeLightBtn = getBtn('globalThemeLightBtn') || getBtn('themeLightBtn');
    const themeDarkBtn = getBtn('globalThemeDarkBtn') || getBtn('themeDarkBtn');

    function updateThemeButtons(theme) {
        if (themeLightBtn) {
            themeLightBtn.classList.toggle('selected', theme === 'light');
        }
        if (themeDarkBtn) {
            themeDarkBtn.classList.toggle('selected', theme === 'dark');
        }
    }

    function setTheme(theme) {
        // Apply to both HTML and Body for maximum CSS selector compatibility
        document.documentElement.setAttribute('data-theme', theme);
        document.body.setAttribute('data-theme', theme);
        localStorage.setItem('smartiesTheme', theme);
        updateThemeButtons(theme);
        
        // Custom event for charts or other components that need to re-render
        document.dispatchEvent(new CustomEvent('themeChanged', { detail: { theme } }));
    }

    if (themeLightBtn) themeLightBtn.addEventListener('click', () => setTheme('light'));
    if (themeDarkBtn) themeDarkBtn.addEventListener('click', () => setTheme('dark'));

    // 3. Initialize saved theme or OS preference
    const savedTheme = localStorage.getItem('smartiesTheme');
    const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    
    if (savedTheme) {
        setTheme(savedTheme);
    } else {
        setTheme(systemPrefersDark ? 'dark' : 'light');
    }
    
    // 4. Sync theme instantly before body renders (if script is in head)
    // This part is for scripts that might be called later, but we call it here too
    const syncTheme = () => {
        const theme = localStorage.getItem('smartiesTheme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
        document.documentElement.setAttribute('data-theme', theme);
        document.body.setAttribute('data-theme', theme);
    };
    syncTheme();
});

// Anti-caching: Ensure the page reloads if the user navigates back after logout
// This prevents the browser from showing a cached version of a protected page
window.addEventListener('pageshow', function(event) {
    if (event.persisted || (window.performance && window.performance.navigation.type === 2)) {
        window.location.reload();
    }
});
