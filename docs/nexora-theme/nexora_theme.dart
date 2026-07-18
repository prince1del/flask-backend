// NEXORA — Flutter theme
// Source of truth: nexora-design-tokens.md
// Requires the `google_fonts` package in pubspec.yaml:
//   dependencies:
//     google_fonts: ^6.0.0
//     flutter_svg: ^2.0.0   (for nexora-icons/*.svg)

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Raw color tokens — keep in sync with nexora-theme.css
class NxColors {
  NxColors._();

  static const void_ = Color(0xFF05070C);
  static const bg1 = Color(0xFF0A0E18);
  static const bg2 = Color(0xFF0E1524);

  static const glass = Color(0x0BFFFFFF); // ~4.5% white
  static const glassHover = Color(0x13FFFFFF); // ~7.5% white
  static const glassBorder = Color(0x17FFFFFF); // ~9% white

  static const cyan = Color(0xFF25E0FF);
  static const cyanDim = Color(0x2925E0FF); // ~16% opacity
  static const violet = Color(0xFF9A6BFF);
  static const violetDim = Color(0x299A6BFF);
  static const mint = Color(0xFF3FE0A5);
  static const amber = Color(0xFFFFB648);
  static const coral = Color(0xFFFF6B6B);

  static const text = Color(0xFFEAF0FB);
  static const textDim = Color(0xFF8B96B8);
  static const textFaint = Color(0xFF4B5474);
}

/// Text styles — Orbitron for display, Sora for UI, JetBrains Mono for data.
/// Use sparingly per role; see nexora-design-tokens.md section 2.
class NxText {
  NxText._();

  static TextStyle display({double size = 23, FontWeight weight = FontWeight.w700}) =>
      GoogleFonts.orbitron(fontSize: size, fontWeight: weight, color: NxColors.text, letterSpacing: 0.02);

  static TextStyle body({double size = 14, FontWeight weight = FontWeight.w400, Color? color}) =>
      GoogleFonts.sora(fontSize: size, fontWeight: weight, color: color ?? NxColors.text);

  static TextStyle bodyDim({double size = 13} ) =>
      GoogleFonts.sora(fontSize: size, fontWeight: FontWeight.w500, color: NxColors.textDim);

  static TextStyle mono({double size = 12, Color? color}) =>
      GoogleFonts.jetBrainsMono(fontSize: size, fontWeight: FontWeight.w500, color: color ?? NxColors.cyan);
}

/// Full app ThemeData for MaterialApp(theme: NxTheme.dark).
class NxTheme {
  NxTheme._();

  static ThemeData get dark {
    final base = ThemeData.dark(useMaterial3: true);

    return base.copyWith(
      scaffoldBackgroundColor: NxColors.void_,
      colorScheme: base.colorScheme.copyWith(
        surface: NxColors.bg1,
        primary: NxColors.cyan,
        secondary: NxColors.violet,
        error: NxColors.coral,
        onSurface: NxColors.text,
        onPrimary: NxColors.void_,
      ),
      textTheme: base.textTheme.copyWith(
        headlineSmall: NxText.display(size: 19),
        titleMedium: NxText.body(size: 16, weight: FontWeight.w600),
        bodyMedium: NxText.body(),
        bodySmall: NxText.bodyDim(),
        labelSmall: NxText.mono(size: 10.5, color: NxColors.textFaint),
      ),
      cardTheme: base.cardTheme.copyWith(
        color: NxColors.glass,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: const BorderSide(color: NxColors.glassBorder, width: 0.5),
        ),
      ),
      appBarTheme: base.appBarTheme.copyWith(
        backgroundColor: Colors.transparent,
        elevation: 0,
        titleTextStyle: NxText.display(size: 17),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: NxColors.cyan,
          foregroundColor: NxColors.void_,
          textStyle: NxText.body(size: 13, weight: FontWeight.w600),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: NxColors.text,
          side: const BorderSide(color: NxColors.glassBorder, width: 0.5),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        ),
      ),
      iconTheme: const IconThemeData(color: NxColors.textDim, size: 20),
      dividerColor: NxColors.glassBorder,
    );
  }
}

/// Status pill — success / warning / danger / info.
/// Mirrors .nx-pill classes in nexora-theme.css.
enum NxStatus { success, warning, danger, info }

class NxStatusPill extends StatelessWidget {
  final String label;
  final NxStatus status;

  const NxStatusPill({super.key, required this.label, required this.status});

  Color get _color => switch (status) {
        NxStatus.success => NxColors.mint,
        NxStatus.warning => NxColors.amber,
        NxStatus.danger => NxColors.coral,
        NxStatus.info => NxColors.cyan,
      };

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
      decoration: BoxDecoration(
        color: _color.withOpacity(0.12),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(label, style: NxText.mono(size: 10.5, color: _color)),
    );
  }
}

/// Pulsing brand mark — the one signature animated element.
/// Use once, near the NEXORA wordmark, never elsewhere.
class NxCoreMark extends StatefulWidget {
  final double size;
  const NxCoreMark({super.key, this.size = 30});

  @override
  State<NxCoreMark> createState() => _NxCoreMarkState();
}

class _NxCoreMarkState extends State<NxCoreMark> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 3200))..repeat(reverse: true);

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _c,
      builder: (context, child) {
        final t = Curves.easeInOut.transform(_c.value);
        return Container(
          width: widget.size,
          height: widget.size,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: RadialGradient(
              center: const Alignment(-0.3, -0.4),
              colors: [NxColors.cyan, NxColors.violet],
            ),
            boxShadow: [
              BoxShadow(
                color: Color.lerp(NxColors.cyan, NxColors.violet, t)!.withOpacity(0.55),
                blurRadius: 14 + (t * 12),
                spreadRadius: 1 + (t * 4),
              ),
            ],
          ),
        );
      },
    );
  }
}
