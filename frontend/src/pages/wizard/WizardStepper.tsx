const STEP_LABELS = [
  "Test Detayları",
  "URL Bilgisi",
  "Persona",
  "Analiz Modülleri",
  "Özet ve Başlat",
];

interface WizardStepperProps {
  currentStep: number;
}

export default function WizardStepper({ currentStep }: WizardStepperProps) {
  return (
    <ol className="wizard-steps" aria-label="Sihirbaz adımları">
      {STEP_LABELS.map((label, index) => {
        const step = index + 1;
        const className =
          step === currentStep ? "is-current" : step < currentStep ? "is-complete" : "";
        return (
          <li
            key={label}
            className={className}
            aria-current={step === currentStep ? "step" : undefined}
          >
            {step}. {label}
          </li>
        );
      })}
    </ol>
  );
}
