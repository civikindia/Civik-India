(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        const page = document.body.dataset.page;
        const helpBtn = document.getElementById('helpTour');

        const supportedPages = ['public.index', 'public.submit_complaint', 'public.geo_heatmap'];

        // If page is not supported, hide the help button and return
        if (!supportedPages.includes(page)) {
            if (helpBtn) {
                helpBtn.style.display = 'none';
            }
            return;
        }

        // Initialize help button trigger
        if (helpBtn) {
            helpBtn.addEventListener('click', function () {
                startTour(true);
            });

            // Pulse help button on first visit
            const tourCompleted = localStorage.getItem('civikindia-tour-completed-' + page);
            if (!tourCompleted) {
                helpBtn.classList.add('pulse');
            }
        }

        // Auto-launch tour on first visit
        setTimeout(function() {
            const tourCompleted = localStorage.getItem('civikindia-tour-completed-' + page);
            if (!tourCompleted) {
                startTour(false);
            }
        }, 1200);

        function startTour(force) {
            if (typeof Shepherd === 'undefined') {
                console.warn('Shepherd.js is not loaded.');
                return;
            }

            // Remove pulse animation once tour is started
            if (helpBtn) {
                helpBtn.classList.remove('pulse');
            }

            const tour = new Shepherd.Tour({
                useModalOverlay: true,
                defaultStepOptions: {
                    exitOnEsc: true,
                    arrow: true,
                    scrollTo: { behavior: 'smooth', block: 'center' },
                    cancelIcon: {
                        enabled: true
                    }
                }
            });

            function getButtons(isFirst, isLast) {
                const buttons = [];
                if (!isLast) {
                    buttons.push({
                        text: 'Skip',
                        classes: 'shepherd-button shepherd-button-secondary',
                        action: function () {
                            tour.cancel();
                        }
                    });
                }
                if (!isFirst) {
                    buttons.push({
                        text: 'Back',
                        classes: 'shepherd-button shepherd-button-secondary',
                        action: function () {
                            tour.back();
                        }
                    });
                }
                buttons.push({
                    text: isLast ? 'Finish' : 'Next',
                    classes: 'shepherd-button shepherd-button-primary',
                    action: isLast ? function () { tour.complete(); } : function () { tour.next(); }
                });
                return buttons;
            }

            if (page === 'public.index') {
                // Homepage Tour steps
                tour.addStep({
                    id: 'welcome',
                    title: 'Welcome to Civik India!',
                    text: 'This is a youth-led public awareness platform helping citizens raise their voices against corruption, bribery, and public service issues completely anonymously.',
                    attachTo: {
                        element: '.home-hero-v2 h1',
                        on: 'bottom'
                    },
                    buttons: getButtons(true, false)
                });

                tour.addStep({
                    id: 'accessibility',
                    title: 'Accessibility Controls',
                    text: 'Adjust font scaling or toggle High Contrast mode for enhanced visual readability.',
                    attachTo: {
                        element: '.gov-accessibility-controls',
                        on: 'bottom'
                    },
                    buttons: getButtons(false, false)
                });

                tour.addStep({
                    id: 'navigation',
                    title: 'Primary Navigation',
                    text: 'Easily navigate to submit complaints, track progress, or view live accountability dashboards and maps.',
                    attachTo: {
                        element: '.portal-nav-list',
                        on: 'bottom'
                    },
                    buttons: getButtons(false, false)
                });

                tour.addStep({
                    id: 'submit-action',
                    title: 'File a Complaint',
                    text: 'Click here to submit your complaint anonymously. No login required.',
                    attachTo: {
                        element: '.hero-primary',
                        on: 'bottom'
                    },
                    buttons: getButtons(false, true)
                });

            } else if (page === 'public.submit_complaint') {
                // Submit Page Tour steps
                tour.addStep({
                    id: 'dept-select',
                    title: 'Choose Department & Service',
                    text: 'First, select the civic department and specific service you want to submit a complaint about.',
                    attachTo: {
                        element: '#department_id',
                        on: 'bottom'
                    },
                    buttons: getButtons(true, false)
                });

                tour.addStep({
                    id: 'description-field',
                    title: 'Complaint Details',
                    text: 'Describe the issue clearly. You can also use the AI Draft Assistant to help structure and polish your description.',
                    attachTo: {
                        element: '#description',
                        on: 'top'
                    },
                    buttons: getButtons(false, false)
                });

                tour.addStep({
                    id: 'evidence-upload',
                    title: 'Upload Evidence',
                    text: 'Attach a relevant document, photo, or PDF supporting your complaint (Max 16MB).',
                    attachTo: {
                        element: '#evidence',
                        on: 'top'
                    },
                    buttons: getButtons(false, false)
                });

                tour.addStep({
                    id: 'map-picker',
                    title: 'Optional Location Pin',
                    text: 'Toggle the interactive map to pin the exact coordinates of the issue for municipal action.',
                    attachTo: {
                        element: '#mapToggleBtn',
                        on: 'top'
                    },
                    buttons: getButtons(false, true)
                });

            } else if (page === 'public.geo_heatmap') {
                // Heatmap Page Tour steps
                tour.addStep({
                    id: 'filters',
                    title: 'Interactive Filters',
                    text: 'Filter civic issues by status, priority, category, or date range to isolate specific density points.',
                    attachTo: {
                        element: '#statusFilter',
                        on: 'bottom'
                    },
                    buttons: getButtons(true, false)
                });

                tour.addStep({
                    id: 'map-view',
                    title: 'Heatmap Visualization',
                    text: 'Explore complaint density across regions. Toggle layer modes between density Heatmap and specific Markers.',
                    attachTo: {
                        element: '#geoHeatmapContainer',
                        on: 'top'
                    },
                    buttons: getButtons(false, false)
                });

                tour.addStep({
                    id: 'map-stats',
                    title: 'Live Map Statistics',
                    text: 'Keep track of the aggregate metrics of all filtered geo-tagged complaints dynamically.',
                    attachTo: {
                        element: '.geo-heatmap-side-col',
                        on: 'left'
                    },
                    buttons: getButtons(false, true)
                });
            }

            // Save state on complete/cancel
            const onTourEnd = function () {
                localStorage.setItem('civikindia-tour-completed-' + page, 'true');
            };

            tour.on('complete', onTourEnd);
            tour.on('cancel', onTourEnd);

            tour.start();
        }
    });
})();
