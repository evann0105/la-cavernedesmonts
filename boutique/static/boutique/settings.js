(() => {
  const linkedAddresses = [
    ['returns_same_as_store', 'return_address'],
    ['business_same_as_store', 'business_address'],
  ];

  document.querySelectorAll('[data-shop-settings-form]').forEach((form) => {
    linkedAddresses.forEach(([checkboxName, addressName]) => {
      const checkbox = form.querySelector(`input[name="${checkboxName}"]`);
      const address = form.querySelector(`textarea[name="${addressName}"]`);
      if (!checkbox || !address) return;

      const syncAddress = () => {
        address.disabled = checkbox.checked;
      };
      syncAddress();
      checkbox.addEventListener('change', syncAddress);
    });
  });
})();
