document.addEventListener('DOMContentLoaded', () => {
    if (typeof Live2DCtrl !== 'undefined') {
        Live2DCtrl.init();
        Live2DCtrl.showMsg('桌面宠物已启动');
    }

    const charBtn = document.getElementById('pet-char-btn');
    if (charBtn && typeof Live2DCtrl !== 'undefined') {
        charBtn.onclick = () => Live2DCtrl.nextCharacter();
    }

    const closeBtn = document.getElementById('pet-close-btn');
    if (closeBtn) {
        closeBtn.onclick = () => window.close();
    }
});
