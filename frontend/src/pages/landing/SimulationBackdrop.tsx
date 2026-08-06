export default function SimulationBackdrop() {
  return (
    <div className="simulation-backdrop" aria-hidden="true">
      <svg viewBox="0 0 1200 640" preserveAspectRatio="xMidYMid slice">
        <g className="simulation-backdrop__orbits">
          <ellipse cx="905" cy="318" rx="238" ry="178" />
          <ellipse cx="905" cy="318" rx="160" ry="112" />
          <path d="M82 486 C250 366 350 530 512 404 S786 204 1032 294" />
          <path d="M142 118 C318 54 390 214 562 168 S842 80 1124 164" />
        </g>

        <g className="simulation-backdrop__connections">
          <path d="M116 430 L252 346 L380 420 L508 296 L664 358" />
          <path d="M690 144 L808 224 L936 174 L1068 264" />
          <path d="M252 346 L298 210 L442 154 L508 296" />
          <path d="M664 358 L782 446 L946 402 L1080 492" />
        </g>

        <g className="simulation-backdrop__nodes">
          <circle cx="116" cy="430" r="5" />
          <circle cx="252" cy="346" r="7" />
          <circle cx="380" cy="420" r="4" />
          <circle cx="508" cy="296" r="7" />
          <circle cx="664" cy="358" r="5" />
          <circle cx="298" cy="210" r="4" />
          <circle cx="442" cy="154" r="6" />
          <circle cx="690" cy="144" r="4" />
          <circle cx="808" cy="224" r="6" />
          <circle cx="936" cy="174" r="4" />
          <circle cx="1068" cy="264" r="7" />
          <circle cx="782" cy="446" r="4" />
          <circle cx="946" cy="402" r="6" />
          <circle cx="1080" cy="492" r="4" />
        </g>

        <g className="simulation-backdrop__signals">
          <circle cx="508" cy="296" r="15" />
          <circle cx="808" cy="224" r="13" />
          <circle cx="946" cy="402" r="14" />
        </g>
      </svg>
    </div>
  );
}
