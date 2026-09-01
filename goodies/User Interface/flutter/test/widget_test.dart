import 'package:flutter_test/flutter_test.dart';
import 'package:inquiry_ui/main.dart';

void main() {
  testWidgets('renders Inquiry identity and connection state', (tester) async {
    await tester.pumpWidget(const InquiryApp());
    expect(find.text('WEB SWEEPER · COLLECTION INQUIRY'), findsOneWidget);
    expect(find.textContaining('Find the signal.'), findsOneWidget);
  });
}
